# 短视频行为记录 Agent

这是一个“剪贴板监听 + 飞书多维表格自动录入”的辅助工具。
旨在帮助你在手机上真实刷短视频时，快速将视频链接和违规判断结果记录到飞书表格中，大幅提升标注和记录效率。

## ⚠️ 核心声明
- **本工具不接管账号**：不登录 TikTok，不要求输入手机号、密码或验证码。
- **本工具不自动化操作**：不替你刷视频，不替你点赞、收藏。
- **推荐工作原理**：你在 iPhone 上复制 TikTok 链接 -> 点悬浮球快捷指令 -> 快捷指令直接把链接发送到 Mac 本地服务 -> Agent 写入飞书。这个模式不依赖 Apple 跨设备剪贴板，速度和准确度最高。
- **备用工作原理**：只复制 TikTok 链接时，Agent 仍会监听 Mac 剪贴板；但该模式依赖 Apple Universal Clipboard，可能慢或漏。
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
   - `链接` (文本/URL)
   - `是否违规` (单选/文本)
   - `FYF密度` (文本)

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

需要创建两个快捷指令：
1. `RecordNoViolation`：提交不违规。
2. `RecordViolation`：提交违规。

英文 iPhone 界面创建 `RecordNoViolation`：
1. Open **Shortcuts**。
2. Tap **+**。
3. Rename shortcut to `RecordNoViolation`。
4. Add action **Get Clipboard**。
5. Add action **URL Encode**，把 `Clipboard` 做 URL Encode。
6. Add action **URL**，内容填：
   ```text
   http://你的Mac局域网IP:8765/record?violation=0&url=URL Encoded Text
   ```
   注意：最后的 `URL Encoded Text` 要选上一步生成的变量，不是手打普通文字。
7. Add action **Get Contents of URL**。
8. Tap **Done**。

英文 iPhone 界面创建 `RecordViolation`：
1. 复制一份 `RecordNoViolation` 快捷指令。
2. Rename shortcut to `RecordViolation`。
3. 把 URL 里的 `violation=0` 改成 `violation=1`。
4. Tap **Done**。

绑定到悬浮球：
1. 打开 iPhone **Settings -> Accessibility -> Touch -> AssistiveTouch**。
2. 打开 **AssistiveTouch**。
3. 在 **Custom Actions** 里，把 **Single-Tap** 绑定到 `RecordNoViolation`。
4. 把 **Long Press** 绑定到 `RecordViolation`。
5. 之后你只需要复制链接后操作悬浮球，不再依赖跨设备剪贴板同步。

### 第四步：开始刷视频！
1. 启动后会自动进入监听状态，看到 `Agent 已启动，正在监听剪贴板` 即可开始刷。
2. 如果视频**不违规**：在 TikTok 点 **Share -> Copy link**，然后**点一下悬浮球**，Agent 会写入 `是否违规=否`。
3. 如果视频**违规**：在 TikTok 点 **Share -> Copy link**，然后**长按悬浮球**，Agent 会写入 `是否违规=是`。
4. 终端会显示编号和判定结果，例如：
   ```text
   #12 判定: 违规
   #12 ✅ 已写入：违规
   ```
5. 你不需要在电脑终端按任何键；如果要退出监听，回到终端按 `q`。

**💡 终极操作提示：**
极速模式的连招是：**TikTok Share -> Copy link** 👉 **不违规点一下悬浮球 / 违规长按悬浮球**。
这个模式是 iPhone 直接通过 Wi-Fi 发给 Mac，不需要等 Apple 跨设备剪贴板。

---

## 💡 常见问题 Q&A

**Q: 为什么我在手机上复制了链接，Mac 终端没反应？**
A: 如果你只复制链接、不点悬浮球，仍然依赖苹果“通用剪贴板”，它可能会慢或漏。推荐使用极速模式：复制链接后点一下悬浮球或长按悬浮球，让 iPhone 直接发给 Mac。

**Q: 为什么极速模式不再推荐“只复制链接”？**
A: 只复制链接必须等 Apple Universal Clipboard 同步到 Mac，速度和准确度不可控。极速模式让 iPhone 快捷指令直接请求 Mac 本地服务，链接和违规状态一次性绑定，最适合高频采集。

**Q: 报错提示“写入字段 xxx 失败”怎么办？**
A: 请检查你的飞书多维表格的表头名字，是否和脚本预期的名字完全一致。如果不一致，请在表格中修改列名。

**Q: 我中途去搜了关键词，怎么记录？**
A: 在 Mac 终端按 `Ctrl+C` 暂停监听返回主菜单，输入 `2`（手动录入搜索词），输入词汇回车后即可写入表格。然后再次输入 `1` 继续监听剪贴板。

**Q: 我在飞书表格里手动修改了数据，会导致 Agent 写乱吗？**
A: 绝对不会。Agent 每次都在表格最底部新建一行。你可以随时在表格里随意修改、备注、删除之前的行，只要不改表头名字，Agent 就不会受影响。
