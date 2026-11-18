# 海外媒体覆盖观察

这个 Python 项目整理与“海外媒体覆盖观察”有关的来源观察，并生成供研究人员复核的统计摘要。

比较两个周期时，可以按当时的来源覆盖范围给出原始观测、覆盖缺口和可比结果，避免把来源停更、恢复或迁移地区造成的沉默误读为零。详见 [docs/analysis-notes.md](docs/analysis-notes.md)。

## 开发

需要 Python 3.11 或更高版本。安装开发依赖：`python -m pip install -e .`。

运行测试：`python -m unittest discover -s tests -v`。
构建发行包：`python -m build`。
