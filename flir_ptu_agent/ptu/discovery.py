from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .config import get_artifacts_dir
from .models import PTUConfig, PTUDiscoveryResult
from .web_client import PTUWebClient


DISCOVERY_KEYWORDS = [
    "cgi",
    "ajax",
    "fetch",
    "xmlhttp",
    "control",
    "ptu",
    "move",
    "pan",
    "tilt",
]


def discover_web_api(
    client: PTUWebClient,
    config: PTUConfig,
    max_page_depth: int = 2,
    max_script_depth: int = 2,
) -> PTUDiscoveryResult:
    artifacts_dir = get_artifacts_dir(config)
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    response = client.fetch_root_page()
    root_html = response.text
    root_path = artifacts_dir / "root.html"
    root_path.write_text(root_html, encoding="utf-8")

    root_url = client.build_url("/")
    page_graph = _crawl_same_origin_pages(
        client=client,
        artifacts_dir=artifacts_dir,
        root_url=root_url,
        root_html=root_html,
        max_depth=max_page_depth,
    )
    page_title = page_graph["page_title"]
    links = page_graph["links"]
    forms = page_graph["forms"]
    script_urls = page_graph["script_urls"]
    fetched_pages = page_graph["fetched_page_urls"]
    scripts, script_keyword_hits, script_html_refs = _fetch_same_origin_scripts(
        client=client,
        initial_script_urls=script_urls,
        artifacts_dir=artifacts_dir,
        max_depth=max_script_depth,
    )

    if script_html_refs:
        extra_page_graph = _crawl_page_urls(
            client=client,
            artifacts_dir=artifacts_dir,
            initial_page_urls=script_html_refs,
            max_depth=max_page_depth,
        )
        links.extend(extra_page_graph["links"])
        forms.extend(extra_page_graph["forms"])
        script_urls.extend(extra_page_graph["script_urls"])
        fetched_pages.extend(extra_page_graph["fetched_page_urls"])
        if page_title is None:
            page_title = extra_page_graph["page_title"]
        html_keyword_hits = _merge_keyword_hits(
            *page_graph["page_keyword_hits"],
            *extra_page_graph["page_keyword_hits"],
        )
        scripts, script_keyword_hits, _ = _fetch_same_origin_scripts(
            client=client,
            initial_script_urls=script_urls,
            artifacts_dir=artifacts_dir,
            max_depth=max_script_depth,
        )
    else:
        html_keyword_hits = _merge_keyword_hits(*page_graph["page_keyword_hits"])

    keyword_hits = _merge_keyword_hits(html_keyword_hits, script_keyword_hits)
    likely_control_endpoints = _collect_likely_endpoints(
        links=links,
        forms=forms,
        keyword_hits=keyword_hits,
        client=client,
    )
    script_inferred_control_endpoints = _collect_confirmed_endpoints(
        client=client,
        scripts=scripts,
        fetched_pages=fetched_pages,
    )
    notes = [
        "Discovery only records likely web control patterns.",
        "Script-inferred PTU control endpoints come from concrete page scripts.",
        "Validated endpoints are promoted only after a real execute path succeeds.",
    ]
    validated_control_endpoints = _load_validated_endpoints(artifacts_dir)

    result = PTUDiscoveryResult(
        base_url=config.base_url,
        fetched_at=datetime.now(timezone.utc).isoformat(),
        page_title=page_title,
        root_status_code=response.status_code,
        fetched_page_urls=sorted(set(fetched_pages)),
        links=sorted(set(links)),
        forms=forms,
        scripts=sorted(set(script_urls)),
        fetched_script_urls=sorted(scripts.keys()),
        keyword_hits=keyword_hits,
        likely_control_endpoints=likely_control_endpoints,
        script_inferred_control_endpoints=script_inferred_control_endpoints,
        validated_control_endpoints=validated_control_endpoints,
        artifacts_dir=str(artifacts_dir),
        notes=notes,
    )

    (artifacts_dir / "discovery.json").write_text(
        json.dumps(asdict(result), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def summarize_discovery(result: PTUDiscoveryResult) -> str:
    return (
        f"PTU discovery summary\n"
        f"- base_url: {result.base_url}\n"
        f"- title: {result.page_title or 'n/a'}\n"
        f"- pages fetched: {len(result.fetched_page_urls)}\n"
        f"- links: {len(result.links)}\n"
        f"- forms: {len(result.forms)}\n"
        f"- scripts referenced: {len(result.scripts)}\n"
        f"- scripts fetched: {len(result.fetched_script_urls)}\n"
        f"- likely control endpoints: {len(result.likely_control_endpoints)}\n"
        f"- script-inferred control endpoints: {len(result.script_inferred_control_endpoints)}\n"
        f"- validated control endpoints: {len(result.validated_control_endpoints)}\n"
        f"- artifacts: {result.artifacts_dir}"
    )


def _extract_links(soup: BeautifulSoup, client: PTUWebClient) -> list[str]:
    links: list[str] = []
    for tag in soup.find_all(["a", "link"]):
        href = tag.get("href")
        if href:
            links.append(client.build_url(href))
    return links


def _extract_script_urls(soup: BeautifulSoup, client: PTUWebClient) -> list[str]:
    urls: list[str] = []
    for tag in soup.find_all("script"):
        src = tag.get("src")
        if src:
            urls.append(client.build_url(src))
    return urls


def _extract_forms(soup: BeautifulSoup, client: PTUWebClient) -> list[dict]:
    forms: list[dict] = []
    for form in soup.find_all("form"):
        fields = []
        for field in form.find_all(["input", "button", "select", "textarea"]):
            fields.append(
                {
                    "tag": field.name,
                    "name": field.get("name"),
                    "type": field.get("type"),
                    "value": field.get("value"),
                }
            )
        forms.append(
            {
                "action": client.build_url(form.get("action", "/")),
                "method": (form.get("method") or "get").upper(),
                "fields": fields,
            }
        )
    return forms


def _crawl_same_origin_pages(
    client: PTUWebClient,
    artifacts_dir: Path,
    root_url: str,
    root_html: str,
    max_depth: int,
) -> dict:
    pending: list[tuple[str, int, str | None]] = [(root_url, 0, root_html)]
    seen: set[str] = set()
    all_links: list[str] = []
    all_forms: list[dict] = []
    all_scripts: list[str] = []
    all_keyword_hits: list[dict[str, list[str]]] = []
    fetched_page_urls: list[str] = []
    page_title: str | None = None

    while pending:
        page_url, depth, known_html = pending.pop(0)
        if page_url in seen or depth > max_depth or not client.is_same_origin(page_url):
            continue
        seen.add(page_url)

        html = known_html if known_html is not None else client.fetch_text(page_url)
        fetched_page_urls.append(page_url)
        artifact_name = _safe_filename(page_url) + ".html"
        (artifacts_dir / artifact_name).write_text(html, encoding="utf-8")

        soup = BeautifulSoup(html, "html.parser")
        if page_title is None and soup.title:
            page_title = soup.title.get_text(strip=True)

        page_links = _extract_links(soup, client)
        page_forms = _extract_forms(soup, client)
        page_scripts = _extract_script_urls(soup, client)
        html_refs = _extract_html_references(html, base_url=page_url)

        all_links.extend(page_links)
        all_forms.extend(page_forms)
        all_scripts.extend(page_scripts)
        all_keyword_hits.append(_scan_keyword_hits(page_url, html))

        next_pages = set(html_refs)
        for link in page_links:
            if _looks_like_html_page(link):
                next_pages.add(link)

        for next_page in sorted(next_pages):
            if next_page not in seen and client.is_same_origin(next_page):
                pending.append((next_page, depth + 1, None))

    return {
        "page_title": page_title,
        "links": all_links,
        "forms": all_forms,
        "script_urls": all_scripts,
        "page_keyword_hits": all_keyword_hits,
        "fetched_page_urls": fetched_page_urls,
    }


def _crawl_page_urls(
    client: PTUWebClient,
    artifacts_dir: Path,
    initial_page_urls: list[str],
    max_depth: int,
) -> dict:
    pending: list[tuple[str, int]] = [(url, 0) for url in initial_page_urls]
    seen: set[str] = set()
    all_links: list[str] = []
    all_forms: list[dict] = []
    all_scripts: list[str] = []
    all_keyword_hits: list[dict[str, list[str]]] = []
    fetched_page_urls: list[str] = []
    page_title: str | None = None

    while pending:
        page_url, depth = pending.pop(0)
        if page_url in seen or depth > max_depth or not client.is_same_origin(page_url):
            continue
        seen.add(page_url)

        html = client.fetch_text(page_url)
        fetched_page_urls.append(page_url)
        artifact_name = _safe_filename(page_url) + ".html"
        (artifacts_dir / artifact_name).write_text(html, encoding="utf-8")

        soup = BeautifulSoup(html, "html.parser")
        if page_title is None and soup.title:
            page_title = soup.title.get_text(strip=True)

        page_links = _extract_links(soup, client)
        page_forms = _extract_forms(soup, client)
        page_scripts = _extract_script_urls(soup, client)
        html_refs = _extract_html_references(html, base_url=page_url)

        all_links.extend(page_links)
        all_forms.extend(page_forms)
        all_scripts.extend(page_scripts)
        all_keyword_hits.append(_scan_keyword_hits(page_url, html))

        next_pages = set(html_refs)
        for link in page_links:
            if _looks_like_html_page(link):
                next_pages.add(link)
        for next_page in sorted(next_pages):
            if next_page not in seen and client.is_same_origin(next_page):
                pending.append((next_page, depth + 1))

    return {
        "page_title": page_title,
        "links": all_links,
        "forms": all_forms,
        "script_urls": all_scripts,
        "page_keyword_hits": all_keyword_hits,
        "fetched_page_urls": fetched_page_urls,
    }


def _fetch_same_origin_scripts(
    client: PTUWebClient,
    initial_script_urls: list[str],
    artifacts_dir: Path,
    max_depth: int,
) -> tuple[dict[str, str], dict[str, list[str]], list[str]]:
    pending: list[tuple[str, int]] = [(url, 0) for url in initial_script_urls if client.is_same_origin(url)]
    seen: set[str] = set()
    fetched: dict[str, str] = {}
    keyword_hits: dict[str, list[str]] = {}
    html_refs: list[str] = []

    while pending:
        script_url, depth = pending.pop(0)
        if script_url in seen or depth > max_depth:
            continue
        seen.add(script_url)

        try:
            text = client.fetch_text(script_url)
        except Exception:
            continue

        fetched[script_url] = text
        artifact_name = _safe_filename(script_url) + ".js.txt"
        (artifacts_dir / artifact_name).write_text(text, encoding="utf-8")
        keyword_hits = _merge_keyword_hits(keyword_hits, _scan_keyword_hits(script_url, text))
        html_refs.extend(_extract_html_references(text, base_url=script_url))

        for nested in _extract_js_urls(text, base_url=script_url):
            if nested not in seen and client.is_same_origin(nested):
                pending.append((nested, depth + 1))

    return fetched, keyword_hits, sorted(set(html_refs))


def _extract_js_urls(text: str, base_url: str) -> list[str]:
    urls: list[str] = []
    for match in re.findall(r'["\\\']([^"\\\']+\.js(?:\?[^"\\\']*)?)["\\\']', text):
        urls.append(urljoin(base_url, match))
    return urls


def _extract_html_references(text: str, base_url: str) -> list[str]:
    urls: list[str] = []
    for match in re.findall(r'["\\\']([^"\\\']+\.html(?:\?[^"\\\']*)?)["\\\']', text):
        urls.append(urljoin(base_url, match))
    return urls


def _scan_keyword_hits(source_name: str, text: str) -> dict[str, list[str]]:
    hits: dict[str, list[str]] = {}
    for line in text.splitlines():
        normalized = line.strip()
        if not normalized:
            continue
        lowered = normalized.lower()
        for keyword in DISCOVERY_KEYWORDS:
            if keyword in lowered:
                hits.setdefault(keyword, []).append(f"{source_name}: {normalized[:240]}")
    return hits


def _merge_keyword_hits(*groups: dict[str, list[str]]) -> dict[str, list[str]]:
    merged: dict[str, list[str]] = {}
    for group in groups:
        for keyword, items in group.items():
            merged.setdefault(keyword, []).extend(items)
    for keyword, items in merged.items():
        merged[keyword] = items[:50]
    return merged


def _collect_likely_endpoints(
    links: list[str],
    forms: list[dict],
    keyword_hits: dict[str, list[str]],
    client: PTUWebClient,
) -> list[dict]:
    candidates: list[dict] = []
    seen_keys: set[tuple[str, str]] = set()

    for url in links:
        lowered = url.lower()
        if any(keyword in lowered for keyword in DISCOVERY_KEYWORDS):
            candidate = {
                "source": "link",
                "url": url,
                "reason": "link contains PTU control-related keyword",
            }
            key = (candidate["source"], candidate["url"])
            if key not in seen_keys:
                candidates.append(candidate)
                seen_keys.add(key)

    for form in forms:
        haystack = " ".join(
            [
                str(form.get("action", "")),
                str(form.get("method", "")),
                json.dumps(form.get("fields", []), ensure_ascii=False),
            ]
        ).lower()
        if any(keyword in haystack for keyword in DISCOVERY_KEYWORDS):
            candidate = {
                "source": "form",
                "url": form["action"],
                "method": form["method"],
                "reason": "form action or field names suggest PTU control",
            }
            key = (candidate["source"], candidate["url"])
            if key not in seen_keys:
                candidates.append(candidate)
                seen_keys.add(key)

    url_pattern = re.compile(r"https?://[^\s\"']+|/[A-Za-z0-9_\-./?=&%]+")
    for keyword, items in keyword_hits.items():
        for item in items:
            if not any(control_word in keyword for control_word in DISCOVERY_KEYWORDS):
                continue
            for match in url_pattern.findall(item):
                url = client.build_url(match) if match.startswith("/") else match
                url = url.rstrip(":;,)")
                parsed = urlparse(url)
                if parsed.scheme not in {"http", "https"}:
                    continue
                lowered = url.lower()
                if not any(token in lowered for token in ["api", "ptcmd", "control", "ptu", "config", ".html", ".js"]):
                    continue
                candidate = {
                    "source": "keyword_hit",
                    "url": url,
                    "reason": f"keyword '{keyword}' found near URL-like string",
                }
                key = (candidate["source"], candidate["url"])
                if key not in seen_keys:
                    candidates.append(candidate)
                    seen_keys.add(key)

    return candidates


def _collect_confirmed_endpoints(
    client: PTUWebClient,
    scripts: dict[str, str],
    fetched_pages: list[str],
) -> list[dict]:
    confirmed: list[dict] = []
    control_page_seen = any(page.endswith("/control.html") for page in fetched_pages)
    control_script_url = next((url for url in scripts if url.endswith("/control.js")), None)
    fmcs_script_url = next((url for url in scripts if url.endswith("/fmcs.js")), None)

    if not control_page_seen or control_script_url is None or fmcs_script_url is None:
        return confirmed

    control_text = scripts[control_script_url]
    fmcs_text = scripts[fmcs_script_url]
    if "/API/PTCmd" not in fmcs_text:
        return confirmed

    if all(token in control_text for token in ["PO", "TO", "H", "PP", "TP", "$.post(\"/API/PTCmd\""]):
        api_url = client.build_url("/API/PTCmd")
        confirmed.append(
            {
                "kind": "status",
                "url": api_url,
                "method": "POST",
                "command": "PP&TP&PD&TD&C",
                "reason": "control.js uses /API/PTCmd for live PTU status updates.",
            }
        )
        confirmed.append(
            {
                "kind": "device_info",
                "url": api_url,
                "method": "POST",
                "command": "V&NN&NM&NI&NS&NA&NG",
                "reason": "index.js uses /API/PTCmd for PTU network and firmware values.",
            }
        )
        confirmed.append(
            {
                "kind": "pan",
                "url": api_url,
                "method": "POST",
                "command_template": "C=I&PS={pan_speed}&TS={tilt_speed}&PO={step}",
                "reason": "control.js sends PO through /API/PTCmd for pan position offsets.",
            }
        )
        confirmed.append(
            {
                "kind": "tilt",
                "url": api_url,
                "method": "POST",
                "command_template": "C=I&PS={pan_speed}&TS={tilt_speed}&TO={step}",
                "reason": "control.js sends TO through /API/PTCmd for tilt position offsets.",
            }
        )
        confirmed.append(
            {
                "kind": "halt",
                "url": api_url,
                "method": "POST",
                "command": "H",
                "reason": "control.js binds the Halt button to PTCmd('H').",
            }
        )

    index_script_url = next((url for url in scripts if url.endswith("/index.js")), None)
    if index_script_url is not None:
        index_text = scripts[index_script_url]
        api_url = client.build_url("/API/PTCmd")
        if "$(\"#minput\").serialize()" in index_text and "SendNetwork" in index_text:
            confirmed.append(
                {
                    "kind": "network_apply",
                    "url": api_url,
                    "method": "POST",
                    "form_fields": ["NN", "NA", "NM", "NI", "NS", "NG"],
                    "reason": "index.js posts the serialized network form to /API/PTCmd when SendNetwork is clicked.",
                }
            )
        if "\"ds\"" in index_text and "SaveNetwork" in index_text:
            confirmed.append(
                {
                    "kind": "network_save",
                    "url": api_url,
                    "method": "POST",
                    "command": "ds",
                    "reason": "index.js posts 'ds' to /API/PTCmd when SaveNetwork is clicked.",
                }
            )
        if "\"df&r\"" in index_text and "ResetNetwork" in index_text:
            confirmed.append(
                {
                    "kind": "network_reset",
                    "url": api_url,
                    "method": "POST",
                    "command": "df&r",
                    "reason": "index.js posts 'df&r' to /API/PTCmd when ResetNetwork is clicked.",
                }
            )

    return confirmed


def _looks_like_html_page(url: str) -> bool:
    lowered = url.lower()
    return lowered.endswith(".html") or lowered.endswith("/")


def _safe_filename(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path.strip("/").replace("/", "_") or "root"
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", f"{parsed.netloc}_{path}")


def _load_validated_endpoints(artifacts_dir: Path) -> list[dict]:
    path = artifacts_dir / "validated_control_endpoints.json"
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]
