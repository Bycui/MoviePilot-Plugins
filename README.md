# MoviePilot-Plugins

个人维护的 MoviePilot 插件仓库。

## 插件列表

### HddolbyFixer · 高清杜比适配

修复高清杜比（HDDolby）站点迁移域名后，在 MoviePilot 中无法搜索资源、无法刷新用户数据、站点测试误报「Cookie 已失效」的问题。

**问题原因**

站点已从 `hddolby.com` 迁移至 `uhddolby.com`，且官方 API 由 `api.{域名}` 并入主站 `www.{域名}/api/v1`，旧 API 子域已不再解析。MoviePilot 内置 HDDolby 模块仍固定请求 `https://api.{域名}`，同时内置站点资源中该站登记的域名仍为 `hddolby.com`，因此出现：

- 站点被判定为「索引器不存在」，搜索时直接跳过
- 用户数据刷新失败
- 站点测试回落到通用 Cookie 检测，误报「Cookie 已失效」

**插件做了什么**

- 搜索接口与用户数据接口统一改用 `https://www.{域名}/api/v1/...`
- 详情页与下载链接按站点实际配置的 url 推导
- 若站点域名被填成 `uhddolby.com`，自动改回 `hddolby.com`，以匹配内置索引器
- 首页浏览与 RSS 刷新的站点域名归一化，消除「站点 uhddolby.com 不存在」报错

**安装**

1. 打开 MoviePilot「设置 → 插件市场」，添加插件仓库地址：

   ```
   https://github.com/Bycui/MoviePilot-Plugins
   ```

2. 在插件市场搜索「高清杜比适配」，安装并启用
3. 启用后立即生效，无需重启；若站点信息未刷新，可重启一次

也可以手动把 `plugins.v2/hddolbyfixer` 放到本地插件源目录（`PLUGIN_LOCAL_REPO_PATHS` 指向的路径）后安装。

**注意事项**

- 插件只修改运行时的类方法，不改动 MoviePilot 源码文件；禁用插件会撤销补丁。
- 启用后会把 `domain` 为 `uhddolby.com` 的站点记录改为 `hddolby.com`，站点 `url` 保持 `https://www.uhddolby.com/`。
- 若站点 `url` 仍指向旧域名 `hddolby.com`，会自动切换为 `https://www.uhddolby.com/`。
- 禁用插件后，若站点域名仍为 `hddolby.com`，内置模块会重新请求不存在的 `api.hddolby.com`，站点会恢复为不可用状态。
- 若站点再次更换域名或 API 路径，需要更新插件内的域名常量。

**版本记录**

| 版本 | 说明 |
| --- | --- |
| v1.0.0 | 初始版本：修补 API 地址并自动修正站点域名 |
| v1.0.1 | 补充种子链域名归一化，修复首页浏览与 RSS 刷新报「站点不存在」 |
| v1.0.2 | 站点地址仍指向旧域名时自动切换到新域名 |
| v1.0.3 | 说明文案修正：启用后立即生效，无需重启 |

## 许可

MIT License，见 [LICENSE](LICENSE)。
