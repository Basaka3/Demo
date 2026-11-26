# 开始作答

题面和结果示意图在蓝桥云课题目区查看。实验首次启动会自动准备素材、Python 环境和 `submission/` 作答工程，其中 `answers/` 有十份空 TXT。请等待环境准备完成，再从0101开始完善工程。

在文件栏右键单击本文件，选择“打开预览”（Open Preview）阅读。需要并排查看时，选择“在侧边打开预览”（Open Preview to the Side）。

## 进入工程并启动

在编辑器菜单选择“终端”→“新建终端”。平台会自动进入素材目录并启用预装 Python，在该终端执行：

```bash
bash tools/start.sh
```

若环境正在准备，请等待完成后再重新打开终端。启动工具会选择预装 Python，在正确目录启动 `submission/app.py`，显示实际服务进程号和模型、索引状态。服务在后台运行，终端可以继续执行采集与建库命令。起步工程显示模型未就绪时，继续完成0101的配置，再重启服务。

作答文件位于 `submission/`。服务入口为 `app.py`，配置为 `config.json`，业务代码在 `src/`，答复模板只需编辑 `submission/prompts/answer.txt`，网页在 `web/index.html`。请直接完善现有工程。

```bash
bash tools/start.sh status
bash tools/start.sh restart
bash tools/start.sh stop
```

三条命令分别查看状态、重启和停止本工程服务。修改配置、模板、业务代码或索引后，用 `restart` 载入新内容。重复运行 `bash tools/start.sh` 会提示当前服务状态。启动失败时，查看工具显示的 `exam/.service.log`。

## 建库和查看网页

完成0301相应配置后，在素材目录运行：

```bash
python tools/build_index.py
bash tools/start.sh restart
```

在实验右侧打开“Web服务”，填写端口 `8080` 预览网页。同机浏览器可访问 `http://127.0.0.1:8080/`。请在页面选择借出馆、拟还馆并填写问题，按0501要求核对实际答复、结论和资料编号。

## 保存结果与交卷

完成一题并核对题面“结果要求”后，在素材目录执行对应采集命令。例如：

```bash
python tools/results.py --question 0101
```

工具输出本次实际结果并保存到 `submission/answers/0101.txt`，其余小题替换题号。0402自动读取启动工具记录的服务进程号。0501采集会通过预装浏览器实际选择馆点、提问，并核对本次网页答复及来源。需要检查已保存文件时，运行：

```bash
python tools/check.py
```

全部已完成成果统一打包：

```bash
python tools/package.py
```

打包工具先采集当前可以取得的逐题结果，更新对应答案并生成 `exam/submission.zip`。采集失败时保留已有答案或空文件，终端会显示需继续完善的项目。工程变化后，重新核对受影响的小题并重新打包。完整接口、字段、结果文件及打包约定见 [接口与工具说明](docs/接口与工具说明.md)。
