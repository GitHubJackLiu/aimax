# AI MAX

桌面 AI 聚合浏览器：常驻置顶的小窗，一个窗口装下 DeepSeek / Kimi / 豆包 / 通义 / 立创商城等站点，
标签式切换、后台保活、自带浏览器式导航条，并支持从本仓库的 Releases 自动检查更新。

## 功能

- **多站点标签**：内置站点 + 自定义站点，切换标签不刷新页面，搜索到的内容一直保留。
- **常驻置顶**：`Alt+D` 随时呼出/隐藏，贴屏幕顶边可自动收起；托盘常驻。
- **浏览器式导航条**：后退 / 前进 / 刷新 / 主页四个矢量图标，随 `CanGoBack` 自动灰显。
- **链接不外跳**：页面里的新窗口链接留在本工具内打开，不弹系统浏览器。
- **深色 / 浅色界面**：只影响自己的外壳，网页内容不受影响。
- **自动更新**：启动 6 秒后静默检查 GitHub Releases，发现新版本弹窗提示，
  下载 → sha256 校验 → 自替换 → 自动重启。

## 版本

当前版本见 `aimax.py` 顶部 `VERSION`，变更历史见 [CHANGELOG.md](CHANGELOG.md)。

## 本地运行

```bash
python -m venv .venv
.venv/Scripts/pip install pywebview pystray pillow
.venv/Scripts/python aimax.py
```

运行需要 Windows 10/11 + WebView2 运行时（系统自带 Edge 即可）。

## 构建

```bash
.venv/Scripts/python -m PyInstaller AIMAX.spec --noconfirm
```

产物：`dist/AIMAX.exe`（单文件，约 22 MB）。

## 发布新版本（自动更新链路）

1. 改 `aimax.py` 里的 `VERSION`，在 `CHANGELOG.md` 记一笔。
2. 构建：`python -m PyInstaller AIMAX.spec --noconfirm`
3. 打包并算出校验值：

   ```bash
   python release.py
   ```

   输出 `dist/AIMAX-v<版本>-win64.zip` 与 sha256。
4. 在本仓库新建 Release，tag 用 `v<版本>`（例如 `v1.2.0`），
   正文写更新说明，附件上传上面的 zip。
5. 老版本客户端下次启动即可收到更新。

> GitHub 会在资产上返回 `digest: sha256:…`，客户端优先用它校验；
> 清单模式则使用清单里的 `sha256` 字段。两者都没有时跳过校验。

## 更新源配置

客户端默认查本仓库的 latest release（`UPDATE_SOURCE` 常量）。
托盘菜单「设置更新地址」可改，支持三种写法：

| 写法 | 含义 |
| --- | --- |
| `GitHubJackLiu/aimax` 或 `https://github.com/GitHubJackLiu/aimax` | 走 GitHub Releases（推荐） |
| `https://any.host/version.json` | 走自建 JSON 清单，格式见下 |
| `off` / `none` / `-` | 关闭自动检查（离线机、内网） |

清单格式：

```json
{
  "version": "1.2.0",
  "url": "https://any.host/AIMAX-v1.2.0-win64.zip",
  "notes": "这次改了什么",
  "sha256": "可选"
}
```

配置落在 `%APPDATA%\AIMAX\config.json`，键名 `update_url`。
GitHub 资产域名在部分网络下较慢，可在 config 里加 `"update_mirror": "https://your-proxy"`，
下载失败时会自动用「镜像前缀 + 原始地址」重试一次。

## 目录

| 路径 | 用途 |
| --- | --- |
| `aimax.py` | 全部源码（单文件） |
| `AIMAX.spec` | PyInstaller 打包配置 |
| `app.ico` / `_mkicon.py` | 应用图标与其生成脚本 |
| `release.py` | 打包发布物 + 计算 sha256 |
| `dist/` | 构建产物（不入库） |
