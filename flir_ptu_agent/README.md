# flir_ptu_agent

`flir_ptu_agent` 是一个独立于 `camera_agent-main` 的最小 PTU 工程项目，用来先把 FLIR PTU-5 的网页诊断、网页探测、配置管理和安全控制入口单独跑通。这个目录不会修改你现有的 `camera_agent-main` 相机链路，后续如果 PTU 网页控制接口确认了，我们再把它接回 camera agent。

## 为什么单独做

当前已经确认：

- 设备是 **Teledyne FLIR PTU-5**
- 网页控制已经在浏览器里验证成功，`PTU Control` 页面可以驱动 `Pan` 和 `Tilt`
- 当前更可信的控制路径是 **Web / HTTP**
- `4000/tcp` 目前超时，不应默认走 socket
- 当前阶段不应默认走串口

所以这个项目优先做一条诚实的 HTTP 路线：

1. 稳定读取配置
2. 稳定检查 PTU 是否在线
3. 拉取网页和基础信息
4. 递归探测 HTML / JS / form / link 里可能存在的控制接口
5. 只有在真实 HTTP endpoint 被谨慎确认后，才允许做小步运动

如果网页控制接口还没有被程序安全确认，这个项目会明确告诉你“控制接口尚未确认”，而不是伪造成功。

## 当前已知设备事实

- Device: `Teledyne FLIR PTU-5`
- Host Name: `PTU-5`
- MAC: `FC:68:3E:50:7C:53`
- Firmware Version: `3.5.2`
- 网页控制在浏览器中已验证成功
- 当前 PTU 使用 DHCP / auto IP，地址会漂移
- 历史上出现过：
  - `169.254.74.177`
  - `169.254.214.194`
- 当前这台 Ubuntu 机器直连 PTU 的有线网卡是：
  - `enp0s31f6`
- 电脑侧已验证过可配置链路本地地址：
  - `169.254.74.100/16`
- 当前阶段确认：
  - `80/tcp` 可通
  - `4000/tcp` 超时

## 已实现的部分

- YAML 配置读取和校验
- HTTP 根页面访问
- 80 端口网络可达性诊断
- 根页面标题 / 状态码 / 头信息读取
- HTML 中的 `link / form / script` 提取
- 同源 JS 递归下载与关键词扫描
- 原始抓取结果保存到本地 `artifacts/`
- 结构化 discovery 结果导出
- 安全控制入口和 CLI
- 基于 live `control.html + control.js` 确认的 `/API/PTCmd` 控制链路
- 基于真实网页命令格式的 `pan / tilt / halt` safe move 实现

## 当前已经真实确认的 HTTP 控制接口

在当前这台 PTU 上，程序已经通过 live 网页内容确认：

- `control.html` 存在并可访问
- `control.js` 明确使用 `/API/PTCmd`
- `halt` 使用命令：
  - `H`
- 实时状态查询使用命令：
  - `PP&TP&PD&TD&C`
- 设备 / 网络基础信息查询使用命令：
  - `V&NN&NM&NI&NS&NA&NG`
- 平移和俯仰的小步位置偏移命令分别为：
  - `C=I&PS={pan_speed}&TS={tilt_speed}&PO={step}`
  - `C=I&PS={pan_speed}&TS={tilt_speed}&TO={step}`

这些不是猜测，是从当前 PTU 网页实际 HTML / JS 中提取并通过只读查询验证过的。

## 当前真实验证状态

- `pan execute` 已验证
- `tilt execute` 已验证
- `halt execute` 已验证
- 已观察到微动
- 命令 `step` 与最终 `PP/TP` 变化量不一定一一对应，后续应做经验标定

## 尚未默认声称实现的部分

- 浏览器里所有更高阶功能页面的完整语义映射
- 除当前已确认命令之外的其他未核实 PT 命令
- 任意“只凭关键词猜到、但没有网页源码证据”的控制 URL

如果未来换了一台 PTU、换了固件、或者地址漂移到别的设备，而 discovery 没能再次确认控制链路，`move-pan`、`move-tilt`、`halt` 仍然会明确报：

- `PTU web control endpoint has not been safely confirmed yet`

这属于项目的保护行为，不是失败掩盖。

## 目录结构

```text
flir_ptu_agent/
├── README.md
├── requirements.txt
├── config/
│   └── ptu.yaml
├── ptu/
│   ├── __init__.py
│   ├── config.py
│   ├── exceptions.py
│   ├── models.py
│   ├── web_client.py
│   ├── discovery.py
│   ├── controller.py
│   ├── diagnostics.py
│   └── cli.py
├── scripts/
│   ├── test_connect.py
│   ├── discover_web_api.py
│   ├── test_status.py
│   └── demo_safe_move.py
└── examples/
    └── minimal_demo.py
```

## Python 版本

- Python 3.10+

## 依赖安装

建议先在项目目录创建虚拟环境：

```bash
cd flir_ptu_agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

当前依赖保持最小化：

- `requests`
- `PyYAML`
- `beautifulsoup4`

## 默认配置

配置文件位于：

`config/ptu.yaml`

默认内容：

```yaml
ptu:
  host: 169.254.214.194
  timeout_sec: 2.0
  verify_http: false
  safe_mode: true
  max_pan_step: 50
  max_tilt_step: 50
  default_scheme: http
  planned_static_ip: 169.254.74.177
  planned_subnet_mask: 255.255.0.0
  planned_gateway: 0.0.0.0
  planned_host_pc_ip: 169.254.74.100
```

注意：

- PTU 地址可能漂移，必要时你要先手动改这里的 `host`
- 本项目不会自动修改 PTU 的网络设置
- 本项目不会自动给 PTU 写固定 IP

## 快速开始

### 1. 连接测试

```bash
cd flir_ptu_agent
python scripts/test_connect.py
```

这个脚本会：

- 读取 YAML 配置
- 检查 80 端口
- 拉取根页面
- 打印标题、状态码、页面长度

### 2. 网页 API 探测

```bash
cd flir_ptu_agent
python scripts/discover_web_api.py
```

这个脚本会：

- 拉取根页面
- 解析所有链接、表单、脚本
- 递归抓取同源 JS
- 在源码里搜索这些关键字：
  - `cgi`
  - `ajax`
  - `fetch`
  - `xmlhttp`
  - `control`
  - `ptu`
  - `move`
  - `pan`
  - `tilt`
- 把抓到的原始结果写到 `artifacts/`

### 3. 状态汇总

```bash
cd flir_ptu_agent
python scripts/test_status.py
```

这个脚本会输出：

- 当前配置
- 网络诊断摘要
- 设备基础信息
- discovery 摘要

### 4. 命令行入口

```bash
cd flir_ptu_agent
python -m ptu.cli check
python -m ptu.cli discover
python -m ptu.cli status
```

### 5. 安全 movement demo

默认 dry-run：

```bash
cd flir_ptu_agent
python scripts/demo_safe_move.py --axis pan --step 10
```

如果未来 discovery 已经**真实确认**了 HTTP endpoint，再显式执行：

```bash
cd flir_ptu_agent
python scripts/demo_safe_move.py --axis pan --step 10 --execute
```

你也可以用 CLI：

```bash
cd flir_ptu_agent
python -m ptu.cli move-pan --step 10
python -m ptu.cli move-pan --step 10 --execute
python -m ptu.cli move-tilt --step 10
python -m ptu.cli move-tilt --step 10 --execute
```

默认规则：

- 不带 `--execute` 就是 dry-run
- 即使带了 `--execute`，如果控制接口还没被安全确认，也不会假装成功
- safe mode 下会限制单步幅度
- 当前默认 safe step 限制来自 `config/ptu.yaml`
- 当前实现优先走小步 position offset，不做循环连续运动
- `--execute` 会在执行前后自动回读 `PP&TP&PD&TD&C`
- 执行时会把 HTTP 响应体、前后状态和命令字符串写入 `artifacts/executions/`

## Step 标定

为什么需要做 step 标定：

- 目前已经真实观察到 `step=10` 不等于最终 `PP/TP` 必然变化 `10`
- 这说明 step 更像控制命令输入，而不是已经标定好的物理/编码器位移
- 后续如果要把 PTU 接回 `camera_agent`，最好先建立一份经验标定表

运行 dry-run：

```bash
cd flir_ptu_agent
python scripts/calibrate_steps.py --axis pan --steps 5,10,20
```

真实执行一个小范围标定：

```bash
cd flir_ptu_agent
python scripts/calibrate_steps.py --axis pan --steps 5,10,20 --execute
python scripts/calibrate_steps.py --axis tilt --steps 5,10,20 --execute
```

脚本行为：

- 每个 step 都会先读一次 `PP&TP&PD&TD&C`
- 再发送一次小步 movement
- 再读一次状态并计算 `delta_PP` / `delta_TP`
- 每次 movement 后都会发送一次 `halt`
- 如果某次失败，会立即停止后续 step
- safe mode 打开时，step 不能超过 `config/ptu.yaml` 里的安全上限

输出文件位置：

- `artifacts/calibration/`
- `artifacts/calibration/latest_summary.json`
- `artifacts/calibration/latest_summary.md`

## Static IP migration

为什么建议固定 PTU IP：

- 当前 PTU 还处于自动获取地址模式，IP 有漂移风险
- 这会让配置文件里的 `host` 不稳定，也会影响后续与 `camera_agent` 的对接

当前推荐的静态地址方案：

- host PC: `169.254.74.100/16`
- PTU static IP: `169.254.74.177`
- subnet mask: `255.255.0.0`
- gateway: `0.0.0.0`

为什么默认不自动改网络：

- 改 PTU 网络设置会立刻中断当前 HTTP 会话
- 如果新地址没有同步改到本机网卡和配置文件，设备会暂时“消失”
- 当前项目虽然已经从网页和 JS 中识别出网络设置相关字段和按钮，但这轮默认仍保持为 **plan-only**

当前已观察到的网页线索：

- 页面字段：`NN`、`NA`、`NM`、`NI`、`NS`、`NG`
- 页面按钮：`SendNetwork`、`ResetNetwork`、`SaveNetwork`
- `index.js` 显示：
  - `SendNetwork` 会把表单序列化后 POST 到 `/API/PTCmd`
  - `SaveNetwork` 会发送 `ds`
  - `ResetNetwork` 会发送 `df&r`

这说明网页层已经暴露了网络配置入口，但当前项目仍然不会默认代替你执行真实网络改写。

生成一份静态 IP 迁移计划：

```bash
cd flir_ptu_agent
python scripts/plan_static_ip.py
```

如果想覆盖计划参数：

```bash
cd flir_ptu_agent
python scripts/plan_static_ip.py \
  --target-static-ip 169.254.74.177 \
  --target-subnet-mask 255.255.0.0 \
  --target-gateway 0.0.0.0 \
  --planned-host-pc-ip 169.254.74.100
```

这个脚本会输出：

- 当前 host / base URL
- 当前网页读回的网络模式与地址信息
- 目标 PTU 静态地址
- 推荐的 host PC 地址
- 执行后 `config/ptu.yaml` 应该改成什么
- 一份保存到 `artifacts/network_changes/` 的计划 artifact

## artifacts 输出

`discover_web_api.py` 和 `python -m ptu.cli discover` 会把抓取结果保存到：

`artifacts/`

其中通常包括：

- `root.html`
- 各个抓到的脚本内容
- `discovery.json`

## 如果 discovery 没有确认真实接口，下一步怎么做

如果程序只能发现“疑似 endpoint”，但不能安全确认，请用浏览器 DevTools 继续抓：

1. 打开 PTU 网页
2. 打开 DevTools 的 `Network`
3. 进入 `PTU Control`
4. 点击一次很小的 `Pan` 或 `Tilt`
5. 观察是否发出了：
   - XHR / fetch
   - form submit
   - GET / POST 到某个 `cgi` / `control` / `move` URL
6. 记录：
   - URL
   - method
   - query params / form fields
   - 是否有 stop / halt 接口

拿到这些真实请求后，再把它补进本项目，才是安全的。

## 已知限制

- 当前项目不会自动修改 PTU 网络设置
- 当前项目不会自动探测所有网卡并改 IP
- 当前项目不会默认走串口
- 当前项目不会默认走 `4000/tcp`
- 当前 discovery 只能说“疑似控制入口”，不能凭关键词就认定接口可执行
- 当前 movement 只对目前已经确认的 `/API/PTCmd` 命令集做了实现
- 当前网络配置写入接口只做到了网页脚本层识别和迁移计划生成，默认不自动执行改网
- 我们还没有在这份 README 里声称“所有 PTU 功能都已完成映射”

## 一个最小示例

```bash
cd flir_ptu_agent
python examples/minimal_demo.py
```

## 安全提醒

- 不要自动把 discovery 发现的 URL 当成可控 PTU 的真实接口
- 不要在没确认参数语义前做大步运动
- 不要做循环连续运动测试
- 不要自动修改 PTU 当前网络配置

这个项目第一版的目标是“把 PTU 网页链路探清楚并工程化”，不是冒进地假设控制已经打通。
