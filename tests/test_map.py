"""audio-midi-tempo-map 的验收测试：15 条全红，tests 不可修改。

每个测试都直接调用 ``tempomap/map.py`` 中尚未实现的桩函数，因此初始
失败原因是 ``NotImplementedError``。事件按 ``[(tick, bpm)]`` 或
``[(tick, us_per_beat)]`` 构造；tick 往返容差为 1 tick。
"""

import pytest

from tempomap.map import (
    TempoError,
    bpm_to_us,
    map_batch,
    normalize_events,
    scale_map,
    seconds_to_tick,
    tempo_at,
    tick_to_seconds,
    validate_map,
)


PPQ = 480


def test_tick_to_seconds_uses_all_segments():
    """缺陷 1：全程用首段 BPM，变速后时间轴会整体偏短。"""
    events = [(0, 120), (960, 60)]

    assert tick_to_seconds(events, 960, PPQ) == pytest.approx(1.0)
    assert tick_to_seconds(events, 1440, PPQ) == pytest.approx(2.0)


def test_segment_integration_accumulates_from_segment_start():
    """缺陷 2：段内积分不从段起点累加，跨段会丢失前几段的时间。"""
    events = [(0, 120), (480, 60), (960, 240)]

    # 0..480 为 0.5 s，480..960 为 1.0 s，960..1440 为 0.25 s。
    assert tick_to_seconds(events, 1440, PPQ) == pytest.approx(1.75)


def test_seconds_to_tick_roundtrip_within_one_tick():
    """缺陷 3：反解用浮点除法并截断，往返会差 2 tick。"""
    events = [(0, 123), (960, 120)]
    ppq = 2 * PPQ

    for source_tick in (1, 2, 17, 479, 480, 959, 960, 961, 4000, 9001):
        seconds = tick_to_seconds(events, source_tick, ppq)
        recovered = seconds_to_tick(events, seconds, ppq)
        assert abs(recovered - source_tick) <= 1, source_tick


def test_same_tick_later_event_wins():
    """缺陷 4：同 tick 取第一个事件，会与 MIDI 的后写覆盖规则相反。"""
    events = [(960, 180), (0, 120), (960, 60)]

    assert normalize_events(events) == [(0, 120), (960, 60)]
    assert normalize_events([(0, 120), (0, 180)]) == [(0, 180)]
    assert events == [(960, 180), (0, 120), (960, 60)]


def test_bpm_to_us_rounds_instead_of_truncating():
    """缺陷 5：BPM 换微秒用截断，长曲累计会漂移 10 ms。"""
    assert bpm_to_us(120) == 500_000
    assert bpm_to_us(123) == 487_805


def test_tempo_at_boundary_uses_later_segment():
    """缺陷 6：边界 tick 归前一段，事件会落在错误速度。"""
    events = [(0, 500_000), (480, 1_000_000)]

    assert tempo_at(events, 479) == pytest.approx(120.0)
    assert tempo_at(events, 480) == pytest.approx(60.0)


def test_scale_map_rewrites_every_segment():
    """缺陷 7：变速不重算后续段，只有首段生效。"""
    scaled = scale_map([(0, 120), (480, 60), (960, 240)], 2.0)

    assert [tick for tick, _ in scaled] == [0, 480, 960]
    assert [bpm for _, bpm in scaled] == pytest.approx([240, 120, 480])


def test_validate_map_rejects_nonpositive_ppq():
    """缺陷 8：ppq<=0 不校验，积分时会除零。"""
    for ppq in (0, -PPQ):
        problems = validate_map([(0, 120)], ppq)
        assert any("ppq" in problem.lower() for problem in problems)


def test_map_batch_isolates_maps_and_rolls_back():
    """缺陷 9：批量共用累加器会让 map 之间串时间。"""
    maps = [
        {"name": "slow", "events": [(0, 60)], "ticks": [0, 480, 960]},
        {"name": "fast", "events": [(0, 240)], "ticks": [0, 480, 960]},
    ]

    result = map_batch(maps, PPQ)
    assert result["total_maps"] == 2
    assert result["map_names"] == ["slow", "fast"]
    assert result["maps"][0]["seconds"] == pytest.approx([0.0, 1.0, 2.0])
    assert result["maps"][1]["seconds"] == pytest.approx([0.0, 0.25, 0.5])

    snapshot = repr(maps)
    bad = maps + [{"name": "bad", "events": [(0, 0)], "ticks": [0]}]
    with pytest.raises(TempoError):
        map_batch(bad, PPQ)
    assert repr(maps) == snapshot


def test_validate_map_reports_negative_tick():
    """缺陷 10：负 tick 不报错，会让时间倒流。"""
    events = [(-1, 120), (0, 60)]

    with pytest.raises(TempoError):
        tick_to_seconds(events, 0, PPQ)

    problems = validate_map(events, PPQ)
    assert any("tick" in problem.lower() for problem in problems)


def test_single_segment_tick_to_seconds_is_linear():
    """组合 1：单段 map 的 tick→秒必须严格线性。"""
    events = [(0, 120)]

    assert tick_to_seconds(events, 0, PPQ) == pytest.approx(0.0, abs=1e-12)
    assert tick_to_seconds(events, 240, PPQ) == pytest.approx(0.25)
    assert tick_to_seconds(events, 480, PPQ) == pytest.approx(0.5)
    assert tick_to_seconds(events, 960, PPQ) == pytest.approx(1.0)


def test_120_bpm_one_beat_is_half_second():
    """组合 2：120 BPM 时一拍（一个四分音符）恰好 0.5 秒。"""
    assert tick_to_seconds([(0, 120)], PPQ, PPQ) == pytest.approx(0.5)


def test_ppq_zero_raises_tempo_error():
    """组合 3：ppq=0 必须抛 TempoError，而不是除零。"""
    with pytest.raises(TempoError):
        tick_to_seconds([(0, 120)], PPQ, 0)
    with pytest.raises(TempoError):
        seconds_to_tick([(0, 120)], 0.5, 0)


def test_empty_map_returns_zero():
    """组合 4：空 map 的时间换算返回 0。"""
    assert tick_to_seconds([], 1920, PPQ) == 0.0
    assert seconds_to_tick([], 2.0, PPQ) == 0


def test_map_batch_preserves_order_on_success():
    """组合 5：批量全成功时，输出顺序与输入顺序一致。"""
    maps = [
        {"name": "mapB", "events": [(0, 500_000)], "ticks": [0, 480]},
        {"name": "mapA", "events": [(0, 60)], "ticks": [0, 480]},
    ]

    result = map_batch(maps, PPQ)
    assert result["total_maps"] == 2
    assert result["map_names"] == ["mapB", "mapA"]
    assert [item["name"] for item in result["maps"]] == ["mapB", "mapA"]
    assert result["maps"][0]["seconds"] == pytest.approx([0.0, 0.5])
    assert result["maps"][1]["seconds"] == pytest.approx([0.0, 1.0])
