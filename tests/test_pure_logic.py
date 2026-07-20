"""纯逻辑单元测试 —— 不调 HTTP、不连 DB、不扫文件。
验证核心业务函数的输入→输出，毫秒级跑完。

测试分层：
  - 本文件 = 单元测试（纯 Python 逻辑，秒级）
  - test_forget_api.py 等 = 集成测试（真 HTTP + 真 PG，需要环境）
"""
import pytest

from service.svc.learning import level_of
from service.svc.recommend import _sigmoid, K, P_MIN, P_MAX, DIFF_MAP, _G_MAP as G_MAP


# ── level_of ── 掌握度 → 等级（补弱/巩固/变式/进阶/未接触） ──

@pytest.mark.parametrize("m, has_data, expected", [
    (0.50, False, "未接触"),   # 无数据，不论 m 多少都"未接触"
    (0.00, True,  "补弱"),     # m < 0.45
    (0.30, True,  "补弱"),
    (0.44, True,  "补弱"),     # 边界：刚好低于 0.45
    (0.45, True,  "巩固"),     # 边界：0.45 进入巩固
    (0.55, True,  "巩固"),
    (0.64, True,  "巩固"),     # 边界：刚好低于 0.65
    (0.65, True,  "变式"),     # 边界：0.65 进入变式
    (0.75, True,  "变式"),
    (0.79, True,  "变式"),     # 边界：刚好低于 0.80
    (0.80, True,  "进阶"),     # 边界：0.80 进入进阶
    (0.95, True,  "进阶"),
])
def test_level_of(m, has_data, expected):
    assert level_of(m, has_data) == expected


# ── _sigmoid ── IRT 核心数学 σ(x) = 1/(1+e^(-x)) ──

def test_sigmoid_zero():
    assert _sigmoid(0.0) == pytest.approx(0.5, abs=1e-6)

def test_sigmoid_positive():
    assert _sigmoid(5.0) == pytest.approx(0.9933, abs=1e-3)

def test_sigmoid_negative():
    assert _sigmoid(-5.0) == pytest.approx(0.0067, abs=1e-3)

def test_sigmoid_symmetric():
    """σ(x) + σ(-x) = 1。"""
    for x in [-3.0, -1.0, 1.0, 3.0]:
        assert _sigmoid(x) + _sigmoid(-x) == pytest.approx(1.0, abs=1e-10)


# ── IRT 完整公式：p = g + (1-g) * σ(K*(m-d)) ──

def test_irt_correct_probability():
    """典型场景：中等学生 + 中等难度选择题 → 验证计算结果。"""
    m = 0.5                                  # 掌握度
    d = DIFF_MAP["适中"]                      # 0.55（难度值）
    g = G_MAP["单选题"]                       # 0.25（猜对概率）
    p = g + (1.0 - g) * _sigmoid(K * (m - d))
    # 手算：m-d = -0.05, K*(m-d) = -0.25, σ(-0.25) ≈ 0.4378
    #       p = 0.25 + 0.75 * 0.4378 = 0.25 + 0.328 = 0.578
    assert p == pytest.approx(0.578, abs=1e-2)


def test_irt_expert_easy():
    """已经掌握的学生做简单题 → p 很高（接近 1 - g*）。"""
    m = 0.95
    d = DIFF_MAP["容易"]                      # 0.15
    g = G_MAP["单选题"]                       # 0.25
    p = g + (1.0 - g) * _sigmoid(K * (m - d))
    assert p > 0.90

def test_irt_weak_hard():
    """补弱学生做难题 → p 很低（但猜对概率兜底）。"""
    m = 0.25
    d = DIFF_MAP["困难"]                      # 0.90
    g = G_MAP["单选题"]                       # 0.25
    p = g + (1.0 - g) * _sigmoid(K * (m - d))
    # p 在 g 之上但很低
    assert 0.25 <= p < 0.35

def test_irt_fillblank_no_guess():
    """填空题（猜对概率=0）vs 单选题 → 同样条件下 p 更低。"""
    m, d = 0.5, 0.55
    p_choice = 0.25 + 0.75 * _sigmoid(K * (m - d))   # 单选题
    p_fill =  0.0  + 1.0  * _sigmoid(K * (m - d))    # 填空题
    assert p_fill < p_choice


# ── ZPD 常量 ──

def test_zpd_constants():
    """P_MIN、P_MAX 是合理的区间。"""
    assert 0 < P_MIN < P_MAX < 1
    assert P_MIN == 0.30
    assert P_MAX == 0.80


# ── DIFF_MAP / G_MAP 常量完整性 ──

def test_diff_map_coverage():
    """DIFF_MAP 覆盖所有 5 档难度值（0~1）。"""
    assert set(DIFF_MAP.keys()) == {"容易", "较易", "适中", "较难", "困难"}
    for d in DIFF_MAP.values():
        assert 0.0 < d < 1.0

def test_g_map_coverage():
    """G_MAP 覆盖 3 种题型的猜对概率。"""
    assert set(G_MAP.keys()) == {"单选题", "多选题", "判断题"}
    assert G_MAP["单选题"] > G_MAP["多选题"]    # 单选题猜对概率更高（选项少）
    assert G_MAP["判断题"] > G_MAP["单选题"]    # 判断题猜对概率最高（只有两个答案）


# ── level_of 额外验证：has_data 独立性 ──

def test_level_of_has_data_true_vs_false():
    """has_data 为 False 时，即使 m 很高也返回'未接触'。"""
    assert level_of(0.95, False) == "未接触"
    assert level_of(0.95, True) == "进阶"


# ── IRT 全部 DIFF_MAP 值都在公式返回合理范围 ──

def test_irt_all_difficulties_in_range():
    """任意难度 + 任意 m 组合，p 应在 [g, 1.0]。"""
    m, g = 0.5, 0.25
    for diff_name, d in DIFF_MAP.items():
        p = g + (1.0 - g) * _sigmoid(K * (m - d))
        assert g <= p <= 1.0, f"{diff_name}(d={d}) → p={p} out of [{g}, 1.0]"

def test_irt_all_g_values_used():
    """所有题型猜对概率下，p 公式都正常。"""
    m, d = 0.5, 0.55
    for type_name, g in G_MAP.items():
        p = g + (1.0 - g) * _sigmoid(K * (m - d))
        assert 0.0 <= p <= 1.0, f"{type_name}(g={g}) → p={p} out of [0, 1]"
