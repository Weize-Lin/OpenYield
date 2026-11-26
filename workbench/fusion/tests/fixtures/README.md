# 只用于软件回归的精简夹具

historical_read_contracts.zip 与 native_read_contracts.zip 合计约1.1 MB，是独立保存材料的派生软件夹具，**不是完整原始证据包，也不是新运行的电路数据**。

构建时只去掉核验不用的波形列，保留所有时间点、所需节点的原数值文本、重复S_EN探针、样本段顺序、原标签和原合格要求。没有抽点、插值或新增求解。
派生清单及其自身记录哈希按派生文件更新，并标明fixture_only；不能把这些哈希当成原始仿真来源。
原包哈希与变换记录在provenance.json及包内fixture_meta.json；native_expected.json来自未变换源数据的既有独立观察投影。
转换前后逐样本的结论、冲突分类、电压、事件、状态和控制观察一致，原文件不改。

夹具中的历史绝对路径是被测试的数据，通过显式include_aliases解析到临时目录；不需要该用户名或历史路径存在。
生产程序仍拒绝未登记的越界/绝对路径回退。不要拿夹具重新估计良率、作新电路验收或替代完整归档。

完整源包与源码分开安排，见[证据登记](../../../../docs/research/sample_verification/ARTIFACTS.json)。包未发布时url为空，不构造失效下载链接；普通软件测试不需要下载完整包。

如已有完整源包，可选用公开build_contract_fixtures.py重建派生夹具。该构建不是运行回归的前提：

```bash
python workbench/fusion/tests/build_contract_fixtures.py \
  --historical-archive /path/to/historical-read-source.zip \
  --native-archive /path/to/two-point-label-verification-785d3bd.zip \
  --output-dir outputs/rebuilt-read-fixtures
```
