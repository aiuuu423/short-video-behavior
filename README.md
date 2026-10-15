# 短视频行为记录工具

这是一个“剪贴板监听 + 飞书多维表格自动录入”的辅助工具。
旨在帮助你在手机上真实刷短视频时，快速将视频链接和违规判断结果记录到飞书表格中，大幅提升标注和记录效率。

## 📱 支持的平台
自动识别并解析以下平台的视频链接（含短链、分享链、Reels/Shorts 等形式）：

| 平台 | 识别的链接形式（示例） |
| --- | --- |
| **TikTok** | `vm.tiktok.com/xxx`、`www.tiktok.com/@user/video/123` |
| **YouTube** | `youtube.com/watch?v=xxx`、`youtu.be/xxx`、`youtube.com/shorts/xxx` |
| **Instagram** | `instagram.com/reel/xxx`、`instagram.com/p/xxx`、`instagram.com/tv/xxx` |
| **Facebook** | `facebook.com/watch/?v=xxx`、`fb.watch/xxx`、`facebook.com/.../videos/xxx` |

无论复制哪个平台的链接，工具都会自动判断来源平台、提取视频 ID 并去重，无需额外设置。

## ⚠️ 核心声明
- **本工具不接管账号**：不登录任何平台，不要求输入手机号、密码或验证码。
- **本工具不自动化操作**：不替你刷视频，不替你点赞、收藏。
- **推荐工作原理**：你在 iPhone 上复制视频链接 -> 点悬浮球快捷指令 -> 快捷指令直接把链接发送到 Mac 本地服务 -> Agent 写入飞书。这个模式不依赖 Apple 跨设备剪贴板，速度和准确度最高。
- **备用工作原理**：只复制视频链接时，Agent 仍会监听 Mac 剪贴板；但该模式依赖 Apple Universal Clipboard，可能慢或漏。
- **安全提醒**：不要把 `.env`、`.env.*`、`data/`、`configs/`、`cache.json` 上传到 GitHub，它们可能包含飞书密钥、表格 Token 或采集数据。

---

## 🛠 准备工作（仅需一次）

### 1. 确保苹果生态的“通用剪贴板”可用
由于本工具运行在 Mac 上，但你是在 iPhone 上刷视频，必须确保两台设备的剪贴板是互通的：
- iPhone 和 Mac 登录**同一个 Apple ID**。
- 两台设备都**开启 Wi-Fi 和蓝牙**。
- 两台设备都开启**接力（Handoff）**功能（设置 > 通用 > 隔空播放与接力）。
- **测试方法**：在 iPhone 上复制一段文字，看能否在 Mac 上 `Command + V` 粘贴出来。

### 2. 准备飞书多维表格
1. 在飞书新建一个**多维表格**（注意：必须是独立的多维表格，不能是文档 Wiki 里插入的表格）。
2. 设置以下表头字段（建议）：
   - `起始时间` (日期)
   - `行为` (单选/文本)
   - `搜索词` (文本)
   - `链接` (**推荐单行文本**；超链接类型也兼容)
   - `是否违规` (单选/文本)
   - `FYF密度` (文本)

   > 关于「链接」列类型：脚本会自动探测该列是**单行文本(文本)**还是**超链接(URL)**并自适应写入，两种都能正常录入，无需改代码。如需强制按文本写入，可在 `.env` 中设置 `LINK_WRITE_MODE=text`（默认 `auto` 按列类型自动选择）。

### 3. 配置飞书应用权限
为了让脚本能把数据写进表格，你需要将授权应用添加到表格中：
1. 打开你刚才建好的多维表格。
2. 点击右上角 `...` (更多) -> `添加应用`。
3. 搜索并添加指定的应用（如：“短视频行为记录助手”）。

---

## 🚀 环境安装

本工具需要 Python 环境。如果你的 Mac 没有安装，请先安装 Python3。

1. 打开 Mac 的“终端”（Terminal）。
2. 进入本工具所在的文件夹（请将路径替换为你实际解压的路径）：
   ```bash
   cd /你的实际路径/短视频行为记录Agent
   ```
3. 安装必需的依赖库：
   ```bash
   pip3 install -r requirements.txt
   ```

---

## 🎮 如何使用

### 第一步：启动 Agent
在终端中运行以下命令：
```bash
python3 main.py
```

### 第二步：配置写入目标
首次运行程序时，至少需要输入以下两项信息（之后运行会自动加载，无需再输）：
1. **配置名称**：随便起，例如“张三的养号表”。
2. **多维表格链接**：直接粘贴你的飞书多维表格完整网址，按回车。

*注：程序会自动解析你的多维表格 Token，无需再手动寻找。*

如果你没有提前在 `.env` 里配置 `DEFAULT_FEISHU_APP_ID` 和 `DEFAULT_FEISHU_APP_SECRET`，程序还会额外询问：
1. **飞书 App ID**
2. **飞书 App Secret**

你可以复制 `.env.example` 为 `.env`，提前填入默认飞书应用凭据。请不要把真实 `.env` 上传到 GitHub。

### 第三步：配置 iPhone 极速直传快捷指令（推荐）
启动 Agent 后，终端会显示类似下面的地址：
```text
HTTP 直传已开启：http://192.168.1.23:8765/record
```
请把你终端里显示的 `http://你的Mac局域网IP:8765/record` 记下来，下面创建快捷指令要用。

只需要创建一个快捷指令：
1. `RecordViolation`：提交违规。

英文 iPhone 界面创建 `RecordViolation`：
1. Open **Shortcuts**。
2. Tap **+**。
3. Rename shortcut to `RecordViolation`。
4. Add action **Get Clipboard**。
5. Add action **URL Encode**，把 `Clipboard` 做 URL Encode。
6. Add action **URL**，内容填：
   ```text
   http://你的Mac局域网IP:8765/record?violation=1&url=URL Encoded Text
   ```
   注意：最后的 `URL Encoded Text` 要选上一步生成的变量，不是手打普通文字。
7. Add action **Get Contents of URL**。
8. Tap **Done**。

绑定到悬浮球：
1. 打开 iPhone **Settings -> Accessibility -> Touch -> AssistiveTouch**。
2. 打开 **AssistiveTouch**。
3. 在 **Custom Actions** 里，把 **Single-Tap（单击）** 绑定到 `RecordViolation`。
4. 把 **Long Press（长按）** 设回 **None**；不违规时不点悬浮球，只复制链接。

### 第四步：开始刷视频！
1. 启动后会自动进入监听状态，看到 `Agent 已启动，正在监听剪贴板` 即可开始刷。
2. 如果视频**不违规**：只在 App 里点 **Share -> Copy link**（TikTok/YouTube/Instagram/Facebook 均可），Agent 会通过剪贴板监听写入 `是否违规=否`。
3. 如果视频**违规**：先点 **Share -> Copy link**，然后**单击一次悬浮球**，Agent 会写入/修正为 `是否违规=是`。
4. 终端会显示编号、来源平台和判定结果，例如：
   ```text
   #12 检测到视频链接（youtube）:
   #12 判定: 违规
   #12 ✅ 已写入：违规
   ```
5. 你不需要在电脑终端按任何键；如果要退出监听，回到终端按 `q`。

**💡 终极操作提示：**
最终连招是：**不违规只复制链接；违规复制链接后单击一次悬浮球**。
不违规依赖 Apple 跨设备剪贴板监听；违规则通过悬浮球快捷指令直传到 Mac，准确度更高。

---

## 💡 常见问题 Q&A

**Q: 为什么不违规只复制链接时，Mac 终端有时没反应？**
A: 不违规只复制链接依赖 Apple Universal Clipboard，同步偶尔会慢或漏。请保持 Mac 和 iPhone 同一 Wi-Fi、蓝牙开启、Handoff 开启。当前最终流程只保留违规单击悬浮球，不再配置不违规快捷指令，避免重复录入。

**Q: 为什么违规要复制链接后单击悬浮球？**
A: 违规单击悬浮球会让 iPhone 直接把剪贴板里的视频链接通过 Wi-Fi 发给 Mac，并带上 `violation=1`，所以比单纯等跨设备剪贴板更稳。

**Q: 支持哪些平台的链接？会不会认错？**
A: 支持 TikTok、YouTube、Instagram、Facebook（含短链、分享链、Reels/Shorts 等形式）。脚本按域名判定平台并提取视频 ID，非这四个平台或非视频链接会被忽略、不写入。

**Q: 「链接」列我建成了超链接（URL）类型，还是文本类型，有影响吗？**
A: 都可以。脚本启动时会自动探测该列类型：文本列就写纯文本，超链接列就写可点击链接对象。推荐用单行文本，便于复制和检索。

**Q: 报错提示“写入字段 xxx 失败”怎么办？**
A: 请检查你的飞书多维表格的表头名字，是否和脚本预期的名字完全一致。如果不一致，请在表格中修改列名。

**Q: 我在飞书表格里手动修改了数据，会导致 Agent 写乱吗？**
A: 绝对不会。Agent 每次都在表格最底部新建一行。你可以随时在表格里随意修改、备注、删除之前的行，只要不改表头名字，Agent 就不会受影响。
