# 分析口径

当前项目的观察重点是source coverage and regional trend。来源信息、观测记录与分析输出分别保存；报告保留生成时使用的来源标识，便于研究人员回看数据范围。输入观测的数值含义由调用方提供，项目不将报道计数解释为受众态度或传播因果。

## 来源覆盖范围

`SourceCatalog` 除了来源当前是否 active，还可以记录来源的历史覆盖区间（`CoverageSpan`）。每个区间是闭区间，表示该来源在该时段内确实处于观察范围内、且归属某个地区。来源暂停、恢复或从一个地区调整到另一个地区时，追加新的区间即可，不需要改写历史。

一个来源在一个周期内的覆盖判断分为四种：

| 状态 | 含义 | 无观测时的读法 |
| --- | --- | --- |
| `covered` | 整段周期都有覆盖 | 真实的零 |
| `partial` | 周期内只有一部分时间有覆盖 | 未知，不能当作零 |
| `uncovered` | 周期内没有覆盖（停更、尚未加入或已退出） | 未知，不能当作零 |
| `unknown` | 来源不在目录中，无法判断覆盖 | 未知 |

判断按地区进行：来源从 A 地区调整到 B 地区后，A 地区在该来源离开后的沉默是 `uncovered`，B 地区在该来源加入前的沉默同样是 `uncovered`，不会被计入任一地区的零值。

## 两周期比较

`compare_periods(..., catalog=catalog)` 在原有键之外，为每个议题同时给出三组数字：

- 原始观测：`start_value` / `end_value` / `start_sources` / `end_sources`，只反映实际收到的观测，不因覆盖范围变化而调整。
- 覆盖范围内：`start_covered_value` / `end_covered_value`，只包含整段周期都有覆盖的来源；-covered 来源没有观测时计为真实的零。
- 可比范围：`comparable_start_value` / `comparable_end_value`，只包含两个周期都整段覆盖的来源。这是唯一可以用来解释“关注是否变化”的口径；新增来源不会制造整体增长的假象，退出来源也不会被当成零。

同时返回：

- `start_coverage_gaps` / `end_coverage_gaps`：未整段覆盖的来源、状态和原因（例如停更的具体日期区间）。
- `caveats`：用自然语言说明哪些变化来自覆盖范围调整而非报道行为。
- `participation`（`compare_periods_with_snapshot`）：逐个来源列出是否进入可比范围及原因，便于复核人员追溯。

未提供 `catalog` 时，覆盖相关键为 `None` 且 `coverage_state` 为 `unverified`：没有覆盖历史，就不能把任何沉默解释为零。空的可比范围返回 `None` 而不是 `0`。

## 报告留痕

`compare_periods_with_snapshot` 与 `create_coverage_report` 会把当时的覆盖快照、比较结果、逐来源参与情况和口径一起冻结进 `AnalysisReport`。之后即使来源停更、恢复、迁移地区或修改目录，已经发出的报告仍保留当时采用的来源范围与口径，复核人员可以直接从报告追到参与或未参与比较的来源及原因。
