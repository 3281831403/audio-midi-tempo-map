"""MIDI 变速映射：tick、BPM 与微秒/拍的分段积分。

公共约定
========
* tick：MIDI 脉冲位置，非负整数；ppq 是每四分音符的 tick 数，正整数。
* 拍：1 拍 = 1 个四分音符。BPM 是每分钟拍数；MIDI Set Tempo 的原生
  单位是微秒/拍（``us_per_beat``，每拍微秒数）。
* 事件：``(tick, value)``。``value`` 小于 10000 时按 BPM 解释，
  ``value`` 大于等于 10000 时按微秒/拍解释；因此同一个 map 可以接受
  ``[(tick, bpm)]`` 或 ``[(tick, us_per_beat)]``。常见可听速度的 BPM
  小于 1000，而微秒/拍通常为几十万，两种写法不会混淆。
* 分段：先按 tick 排序并对同 tick 去重（输入中靠后者胜）。每两个事件
  之间为一段；首段从 tick 0 起生效，末段延续到任意大的 tick。段内
  BPM 恒定，因此 tick→秒 是线性的。
* 边界：tick 恰好落在事件上时，新速度从该 tick 起生效，即
  :func:`tempo_at` 取后一段；积分到该边界的时间仍由前一段累计得到，
  时间轴保持连续。
* 容差：tick 与秒往返换算误差不超过 1 tick；秒值本身不做取整。
* 异常：非法 map、非正 BPM / 微秒/拍、非正 ppq、负 tick、负时间、
  非法 ratio 等统一抛 :class:`TempoError`。:func:`validate_map` 例外，
  它返回问题列表而不抛出。
"""

import bisect
import math

#: value 小于该阈值按 BPM 解释，否则按微秒/拍解释。
TEMPO_VALUE_THRESHOLD = 10000

#: 每分钟 / 每秒的微秒数。
MICROS_PER_MINUTE = 60_000_000
MICROS_PER_SECOND = 1_000_000

#: 空 map 时 :func:`tempo_at` 返回的 MIDI 默认速度。
DEFAULT_BPM = 120.0


class TempoError(ValueError):
    """速度映射、tick 或 ppq 非法时抛出的异常。"""


# ---------------------------------------------------------------------------
# 内部辅助
# ---------------------------------------------------------------------------

def _is_real_number(value) -> bool:
    """是否为非布尔的 int/float。"""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_finite_positive(value) -> bool:
    return _is_real_number(value) and math.isfinite(value) and value > 0


def _round_half_up(value: float) -> int:
    """四舍五入，恰为 .5 时向上（区别于内置 round 的银行家舍入）。"""
    return math.floor(value + 0.5)


def _to_bpm(value: float) -> float:
    """把 BPM 或微秒/拍统一换算成 BPM，调用方需先校验 value 合法。"""
    if value < TEMPO_VALUE_THRESHOLD:
        return float(value)
    return MICROS_PER_MINUTE / value


def _seconds_per_tick(value: float, ppq: int) -> float:
    """该速度下每个 tick 对应的秒数：一拍时长 / ppq。"""
    if value < TEMPO_VALUE_THRESHOLD:
        # 60 秒/分钟 ÷ BPM 拍/分钟 = 每拍秒数，再除以每拍 tick 数。
        return 60.0 / (float(value) * ppq)
    return float(value) / MICROS_PER_SECOND / ppq


def _check_ppq(ppq: int) -> None:
    if not isinstance(ppq, int) or isinstance(ppq, bool) or ppq <= 0:
        raise TempoError(f"non-positive ppq: {ppq!r}")


def _check_tick(tick, what: str = "tick") -> None:
    if not isinstance(tick, int) or isinstance(tick, bool) or tick < 0:
        raise TempoError(f"negative or non-integer {what}: {tick!r}")


def _prepare_events(events: list, ppq=None):
    """排序去重并做完整合法性校验，返回归一化后的事件列表。

    每个事件的 value 仍保留原始写法（BPM 或微秒/拍），换算在积分时
    按需进行，避免提前取整损失精度。``ppq`` 给定时一并校验。
    """
    if ppq is not None:
        _check_ppq(ppq)
    normalized = normalize_events(events)
    for tick, value in normalized:
        # tick 必须是非负整数；负 tick 会让时间倒流。
        if not isinstance(tick, int) or isinstance(tick, bool) or tick < 0:
            raise TempoError(f"negative or non-integer tick: {tick!r}")
        if not _is_finite_positive(value):
            raise TempoError(f"non-positive bpm: {value!r}")
    return normalized


def _segment_starts(normalized: list) -> list:
    """各段的起始 tick：首段固定从 0 开始，其后取各事件自身的 tick。"""
    if not normalized:
        return []
    return [0] + [tick for tick, _ in normalized[1:]]


def _integrate(normalized: list, ticks: int, ppq: int) -> float:
    """已校验事件上的 tick→秒 分段积分，跨段从段起点继续累加。"""
    if not normalized:
        return 0.0
    starts = _segment_starts(normalized)
    elapsed = 0.0
    for i, (_, value) in enumerate(normalized):
        start = starts[i]
        spt = _seconds_per_tick(value, ppq)
        if i + 1 >= len(normalized):
            # 末段开放延伸到任意大的 tick。
            return elapsed + (ticks - start) * spt
        end = starts[i + 1]
        if ticks < end:
            return elapsed + (ticks - start) * spt
        # 整段走完，累计时间带入下一段。
        elapsed += (end - start) * spt
    return elapsed


# ---------------------------------------------------------------------------
# 公共 API
# ---------------------------------------------------------------------------

def normalize_events(events: list) -> list:
    """按 tick 升序排序并按 tick 去重，同 tick 的后写事件胜出。

    参数
    ----
    events : list[tuple[int, float]]
        事件序列，每项为 ``(tick, value)``，``value`` 是 BPM 或
        微秒/拍。不要求输入已排序；允许同一个 tick 出现多次。

    返回
    ----
    list[tuple[int, float]]
        新列表，按 tick 严格递增；同一个 tick 只保留输入顺序中最后
        一个事件的值。不同 tick 按 tick 升序排列。输入列表和元组均
        不被修改。

    说明
    ----
    本函数只负责排序和去重，不检查 ppq，也不检查 tempo 是否为正；
    负 tick 会原样保留，交给 :func:`validate_map` 报告。事件不是
    二元组等结构性错误抛 :class:`TempoError`。
    """
    try:
        iterator = iter(events)
    except TypeError:
        raise TempoError(f"invalid event: {events!r}")

    # 同名键后写覆盖先写，天然实现 MIDI 的“同 tick 后写胜出”。
    latest = {}
    for event in iterator:
        if not isinstance(event, (tuple, list)) or len(event) != 2:
            raise TempoError(f"invalid event: {event!r}")
        tick, value = event
        latest[tick] = value
    return sorted((tick, value) for tick, value in latest.items())


def bpm_to_us(bpm: float) -> int:
    """把 BPM 换算成 MIDI 的微秒/拍，四舍五入到最近整数。

    参数
    ----
    bpm : int | float
        每分钟四分音符数，必须是有限正数。

    返回
    ----
    int
        每拍微秒数，计算式为::

            60_000_000 / bpm

        然后四舍五入到最近整数（恰为 .5 时向上），不得截断。例如
        ``bpm_to_us(120) == 500000``，``bpm_to_us(123) == 487805``，
        而直接截断会得到 487804，长曲会累计漂移 10 ms 量级。

    ``bpm <= 0``、``NaN`` 或 ``inf`` 抛 :class:`TempoError`。
    """
    if not _is_finite_positive(bpm):
        raise TempoError(f"non-positive bpm: {bpm!r}")
    return _round_half_up(MICROS_PER_MINUTE / bpm)


def tick_to_seconds(events: list, ticks: int, ppq: int) -> float:
    """把单个 tick 换算成从 tick 0 起经过的秒数，跨段积分。

    参数
    ----
    events : list[tuple[int, float]]
        速度 map，事件第二项可用 BPM 或微秒/拍表示。函数内部先按
        :func:`normalize_events` 排序去重。
    ticks : int
        待换算的单个非负 tick（参数名沿用复数形式，本函数按标量
        处理）。
    ppq : int
        每四分音符的 tick 数，必须为正整数。

    返回
    ----
    float
        从 tick 0 到 ``ticks`` 的累计秒数。每段内按恒定速度线性积分，
        跨段时从上一段的累计时间继续累加，不得只用当前段的局部偏移。
        首段速度从 tick 0 起生效（即使首个事件的 tick 大于 0）；若
        ``events`` 为空，任意非负 ``ticks`` 都返回 ``0.0``。

    边界
    ----
    tick 恰好等于某个事件时，该 tick 的累计时间由前一段积分得到，事件
    的新速度从该 tick 之后生效，保证时间连续。

    非法事件（负 tick、非正 tempo 等）、``ticks < 0``、``ppq <= 0``
    抛 :class:`TempoError`。tick 与秒往返误差不超过 1 tick。
    """
    normalized = _prepare_events(events, ppq)
    _check_tick(ticks)
    return _integrate(normalized, ticks, ppq)


def seconds_to_tick(events: list, seconds: float, ppq: int) -> int:
    """把从 tick 0 起经过的秒数反向换算成 tick，结果四舍五入。

    参数
    ----
    events : list[tuple[int, float]]
        速度 map，事件第二项可用 BPM 或微秒/拍表示。
    seconds : float
        非负的累计秒数，必须是有限值。
    ppq : int
        每四分音符的 tick 数，必须为正整数。

    返回
    ----
    int
        最近的 tick。先按段定位，再用该段的恒定速度反解，最后四舍五入
        到最近整数（恰为 .5 时向上），不得直接截断浮点商。与
        :func:`tick_to_seconds` 往返时误差不超过 1 tick；恰好落在段
        边界上的秒数应返回该边界 tick。空 map 返回 ``0``。

    负秒数、非有限秒数、非法事件、``ppq <= 0`` 抛
    :class:`TempoError`。
    """
    normalized = _prepare_events(events, ppq)
    if not _is_real_number(seconds) or not math.isfinite(seconds) or seconds < 0:
        raise TempoError(f"negative or non-finite seconds: {seconds!r}")
    if not normalized:
        return 0

    starts = _segment_starts(normalized)
    elapsed = 0.0
    for i, (_, value) in enumerate(normalized):
        start = starts[i]
        spt = _seconds_per_tick(value, ppq)
        if i + 1 >= len(normalized):
            return _round_half_up(start + (seconds - elapsed) / spt)
        end = starts[i + 1]
        segment_seconds = (end - start) * spt
        if seconds < elapsed + segment_seconds:
            return _round_half_up(start + (seconds - elapsed) / spt)
        # 越过整段：把该段累计时间带入下一段继续定位。
        elapsed += segment_seconds
    # 理论上不可达：末段是开放区间。
    raise TempoError(f"seconds out of range: {seconds!r}")


def tempo_at(events: list, tick: int) -> float:
    """返回指定 tick 所在段的速度，单位为 BPM。

    参数
    ----
    events : list[tuple[int, float]]
        速度 map，事件第二项可用 BPM 或微秒/拍表示。
    tick : int
        非负 tick。

    返回
    ----
    float
        ``tick`` 所在段的 BPM。若事件本身是微秒/拍，先按
        ``60_000_000 / us_per_beat`` 换算为 BPM。tick 恰好落在事件上
        时取该事件（后一段）的速度，不能归到前一段；首段从 tick 0 起
        生效。空 map 返回 MIDI 默认的 ``120.0`` BPM。

    负 tick、非法事件等抛 :class:`TempoError`。
    """
    normalized = _prepare_events(events)
    _check_tick(tick)
    if not normalized:
        return DEFAULT_BPM

    boundaries = [event_tick for event_tick, _ in normalized]
    # bisect_right：tick 恰好等于事件 tick 时索引落在该事件上（后一段）；
    # tick 早于首个事件时回退到首段（首段从 tick 0 生效）。
    index = bisect.bisect_right(boundaries, tick) - 1
    if index < 0:
        index = 0
    value = normalized[index][1]
    return _to_bpm(value)


def scale_map(events: list, ratio: float) -> list:
    """整体变速：保持 tick 不变，把所有段的 BPM 乘以 ratio。

    参数
    ----
    events : list[tuple[int, float]]
        速度 map，事件第二项可用 BPM 或微秒/拍表示。
    ratio : float
        速度比例，必须是有限正数；``ratio > 1`` 加快，``ratio < 1``
        放慢。

    返回
    ----
    list[tuple[int, float]]
        新 map，格式为 ``[(tick, new_bpm), ...]``，按 tick 升序。每一个
        段的 BPM 都要按 ``bpm * ratio`` 重算，不能只改首段；输入若是
        微秒/拍，先换算成 BPM 再缩放，输出统一为 BPM。空 map 返回
        ``[]``。

    非法 ratio 或非法 map 抛 :class:`TempoError`，输入不被修改。
    """
    if not _is_finite_positive(ratio):
        raise TempoError(f"invalid scale ratio: {ratio!r}")
    normalized = _prepare_events(events)
    return [(tick, _to_bpm(value) * ratio) for tick, value in normalized]


def validate_map(events: list, ppq: int) -> list[str]:
    """返回速度 map 的问题列表，而不是抛出异常。

    参数
    ----
    events : list
        待检查的事件序列。检查前按 :func:`normalize_events` 的规则排序
        去重，同 tick 的后写事件覆盖先写事件。
    ppq : int
        每四分音符的 tick 数。

    返回
    ----
    list[str]
        按事件顺序排列的问题，最后追加 ppq 问题；没有问题时返回
        ``[]``。每个问题使用稳定的英文前缀，便于测试检查：

        - ``"negative tick: <tick>"``：tick 小于 0；
        - ``"non-positive bpm: <value>"``：tempo 小于等于 0 或不是
          有限值；
        - ``"non-positive ppq: <ppq>"``：ppq 小于等于 0；
        - ``"invalid event: <repr>"``：事件结构非法。

    本函数不修改输入，也不抛 :class:`TempoError` 来表示 map 内容问题。
    """
    problems: list[str] = []

    # 结构非法的事件不入 map，只记录；其余按后写覆盖去重。
    latest = {}
    try:
        iterator = iter(events)
    except TypeError:
        return [f"invalid event: {events!r}", f"non-positive ppq: {ppq!r}"]
    for event in iterator:
        if not isinstance(event, (tuple, list)) or len(event) != 2:
            problems.append(f"invalid event: {event!r}")
            continue
        tick, value = event
        latest[tick] = value

    for tick, value in sorted(latest.items()):
        if isinstance(tick, int) and not isinstance(tick, bool) and tick < 0:
            problems.append(f"negative tick: {tick}")
        if not _is_finite_positive(value):
            problems.append(f"non-positive bpm: {value}")

    if not isinstance(ppq, int) or isinstance(ppq, bool) or ppq <= 0:
        problems.append(f"non-positive ppq: {ppq}")
    return problems


def map_batch(maps: list, ppq: int) -> dict:
    """批量把多张 map 的 tick 列表换算成秒，任一 map 非法则整体回滚。

    参数
    ----
    maps : list[dict]
        有序列表，每项为::

            {"name": str,
             "events": [(tick, bpm) 或 (tick, us_per_beat), ...],
             "ticks": [int, ...]}

        ``ticks`` 可以为空列表；``events`` 中的两种速度写法都可使用。
    ppq : int
        每四分音符的 tick 数，必须为正整数。

    返回
    ----
    dict
        ``{"maps": [{"name": str, "seconds": list[float]}, ...],
        "map_names": list[str], "total_maps": int}``。
        ``maps``、``map_names`` 的顺序必须与输入完全一致。每张 map 都
        独立从 tick 0 开始积分，不能共用上一张 map 的累加器。空
        ``maps`` 返回
        ``{"maps": [], "map_names": [], "total_maps": 0}``。

    全有或全无：先校验 ppq、每张 map 的名称、事件和 ticks，再计算；
    任一 map 非法（缺字段、非正 tempo、负 tick、负目标 tick、非整数
    ticks 等）都抛 :class:`TempoError`，不返回部分结果，也不修改输入。
    """
    _check_ppq(ppq)
    if not isinstance(maps, list):
        raise TempoError(f"invalid maps batch: {maps!r}")

    # 第一阶段：全量校验并预处理，任一非法立即抛错，此时尚未产生输出。
    prepared = []
    for item in maps:
        if not isinstance(item, dict):
            raise TempoError(f"invalid map entry: {item!r}")
        missing = {"name", "events", "ticks"}.difference(item)
        if missing:
            raise TempoError(f"map entry missing fields: {sorted(missing)}")
        name = item["name"]
        events = item["events"]
        ticks = item["ticks"]
        if not isinstance(name, str):
            raise TempoError(f"invalid map name: {name!r}")
        if not isinstance(ticks, list):
            raise TempoError(f"invalid ticks list: {ticks!r}")
        for tick in ticks:
            _check_tick(tick, what="target tick")
        normalized = _prepare_events(events, ppq)
        prepared.append((name, normalized, ticks))

    # 第二阶段：每张 map 独立从 tick 0 积分，累加器互不串用。
    output_maps = []
    map_names = []
    for name, normalized, ticks in prepared:
        seconds = [_integrate(normalized, tick, ppq) for tick in ticks]
        output_maps.append({"name": name, "seconds": seconds})
        map_names.append(name)

    return {"maps": output_maps, "map_names": map_names,
            "total_maps": len(map_names)}
