# audio-midi-tempo-map

## 题面

导入带变速的 MIDI 后，后半段音符全体提前、时间轴对不上：单段 map 的 tick→秒本应线性，跨段却全程用首段 BPM，变速后时间轴全错；段内积分不按段起点累加，跨段跳跃；反向换算用浮点除法，往返差 2 tick；同 tick 事件取第一个，与 MIDI 规范相反；BPM 换成微秒用截断，长曲漂移 10 ms；边界 tick 归前一段，事件落在错误速度；变速不重算后续段，只有首段生效；ppq<=0 不校验，除零；批量共用累加器，map 之间串；负 tick 不报错，时间倒流。tempomap/map.py 是核心，tests/test_map.py 15个红。要修的是：按 tick 建立分段速度映射并在段内线性积分，跨段从段起点继续累加；同 tick 采用后写覆盖；BPM 与微秒/拍四舍五入互换；边界 tick 使用后一段速度；整体变速重算所有段；统一校验 ppq、tempo 与负 tick；批量按 map 独立积分，任一 map 非法时整体回滚。

## 运行方式

在仓库根目录执行：

```powershell
python -m pytest tests -q
```

初始（未实现）状态下必须恰好输出 `15 failed`，且没有 collection error、import error 或 syntax error。输出尾部形如：

```text
15 failed in 0.05s
```

每个测试都直接调用 `tempomap/map.py` 中的桩函数，当前失败原因是 `NotImplementedError`；实现完成后应变为 `15 passed`。

## 字段表

| 字段 | 取值 |
| --- | --- |
| 所属领域 | 游戏、图形与多媒体 |
| 子方向 | 音视频处理 |
| 任务类型 | Bugfix |
| 语言/框架 | Python 3, pytest |
| 环境可复现等级 | 无外部依赖 |

## 约束

- 只使用 Python 标准库（`bisect`、`math`）与 pytest；离线运行，不使用随机数，不调用 `sleep`。
- tick 为非负整数，ppq 为正整数；BPM 与微秒/拍均为正数；tick 与秒的往返容差为 1 tick。
- 事件使用 `[(tick, bpm)]` 或 `[(tick, us_per_beat)]` 表示；异常统一为 `tempomap.map.TempoError`。
- `tests/test_map.py` 是验收测试，不可修改；不要通过改测试来让结果变绿。

## 目录结构

```text
audio-midi-tempo-map/
├── README.md
├── conftest.py
├── .gitignore
├── tempomap/
│   ├── __init__.py
│   └── map.py
└── tests/
    └── test_map.py
```
