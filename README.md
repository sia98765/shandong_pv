# 山东光伏情报站 - GitHub Actions 自动采集版

这个项目用于每天自动采集山东光伏相关的政策、市场分析、项目信息，并在网页中展示结果。

## 项目文件说明

```text
crawler.py                         # 百度检索采集脚本
index.html                         # 前端展示页面
data/shandong_pv_data.json         # 采集结果，首次运行后自动更新
data/meta.json                     # 数据统计信息，首次运行后自动更新
requirements.txt                   # Python 依赖
.github/workflows/crawler.yml      # GitHub Actions 自动运行配置
.nojekyll                          # GitHub Pages 静态部署配置
logs/blocked_pages/                # 如果触发百度验证，会保存截图/HTML
```

## 一、小白部署步骤

### 1. 新建 GitHub 仓库

1. 打开 GitHub。
2. 点击右上角 `+`。
3. 选择 `New repository`。
4. 仓库名可以填：`shandong-pv-actions`。
5. 建议选择 `Public`。
6. 点击 `Create repository`。

### 2. 上传项目文件

1. 解压本压缩包。
2. 进入解压后的文件夹。
3. 把里面的所有文件上传到 GitHub 仓库根目录。
4. 注意必须包含这个目录：

```text
.github/workflows/crawler.yml
```

如果网页上传时没有带上 `.github` 文件夹，可以手动新建：

```text
.github/workflows/crawler.yml
```

然后把根目录下备用文件 `github-actions-crawler.yml` 的内容复制进去。

### 3. 开启 GitHub Actions 写入权限

1. 进入仓库 `Settings`。
2. 点击左侧 `Actions`。
3. 点击 `General`。
4. 找到 `Workflow permissions`。
5. 选择：`Read and write permissions`。
6. 勾选允许 Actions 创建和批准 pull requests 可不选。
7. 点击 `Save`。

这一步很重要，否则 Actions 跑完后没有权限把 `data/shandong_pv_data.json` 提交回仓库。

### 4. 手动运行一次采集

1. 进入仓库 `Actions`。
2. 左侧选择：`山东光伏情报站 - 百度检索自动更新`。
3. 点击右侧 `Run workflow`。
4. 再点击绿色 `Run workflow`。
5. 等待任务执行完成。

脚本默认使用：

```bash
xvfb-run -a -s "-screen 0 1366x900x24" python crawler.py \
  --overwrite \
  --no-file-log \
  --no-detail-logs \
  --limit-queries 0 \
  --max-pages-per-query 1 \
  --query-sleep-min 20 \
  --query-sleep-max 35 \
  --page-sleep-min 3 \
  --page-sleep-max 8 \
  --after-load-sleep 3 \
  --restart-browser-every 3 \
  --restart-sleep 60
```

含义：每查询 3 个关键词就关闭浏览器，等待 60 秒后重新打开，降低百度验证概率。

### 5. 开启网页访问 GitHub Pages

1. 进入仓库 `Settings`。
2. 点击左侧 `Pages`。
3. `Build and deployment` 选择：`Deploy from a branch`。
4. Branch 选择：`main`。
5. 目录选择：`/ root`。
6. 点击 `Save`。
7. 等几分钟，GitHub 会显示一个访问地址，例如：

```text
https://你的用户名.github.io/shandong-pv-actions/
```

打开这个地址就能看到前端页面。

## 二、修改自动运行时间

打开：

```text
.github/workflows/crawler.yml
```

找到：

```yaml
schedule:
  - cron: "0 1 * * *"
```

GitHub Actions 的 cron 使用 UTC 时间。

常用示例：

```yaml
# 每天 UTC 01:00，北京时间 09:00
- cron: "0 1 * * *"

# 每天 UTC 02:00，北京时间 10:00
- cron: "0 2 * * *"

# 每周一 UTC 01:00 运行
- cron: "0 1 * * 1"
```

## 三、如果百度触发验证怎么办

百度对 GitHub Actions 的机房 IP 比较敏感。如果触发验证，脚本会保存调试文件到：

```text
logs/blocked_pages/
```

并且 GitHub Actions 会上传一个 artifact：

```text
baidu-blocked-pages
```

可以在 Actions 任务页面底部下载查看。

建议优化参数：

```bash
--max-pages-per-query 1
--query-sleep-min 30
--query-sleep-max 60
--restart-browser-every 2
--restart-sleep 120
```

如果仍然经常验证，建议不要用 GitHub Actions 采集百度，可以改成本地电脑或自己的服务器定时运行。

## 四、本地 Windows 调试

先安装依赖：

```bash
pip install -r requirements.txt
```

运行少量测试：

```bash
python crawler.py --overwrite --debug --limit-queries 3 --max-pages-per-query 1
```

本地 Windows 不需要 `xvfb-run`。

## 五、常见问题

### 1. Actions 运行成功，但是网页没有数据

先检查仓库里的文件是否更新：

```text
data/shandong_pv_data.json
```

如果这个文件还是空的，说明采集结果被过滤掉了，或者百度返回了验证页面。

### 2. Actions 报 `Permission denied to github-actions[bot]`

说明没有开启写入权限。按照上面的「开启 GitHub Actions 写入权限」重新设置。

### 3. GitHub Pages 打开后样式正常，但数据加载失败

检查浏览器地址是否是 GitHub Pages 地址，不要直接双击打开本地 HTML。Pages 部署成功后，`index.html` 会自动读取：

```text
./data/shandong_pv_data.json
```

### 4. 想少采集一点，降低风控

修改 `.github/workflows/crawler.yml`：

```bash
--limit-queries 6
--max-pages-per-query 1
```

`--limit-queries 6` 表示只跑前 6 个查询任务。
