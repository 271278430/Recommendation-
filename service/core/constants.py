"""掌握度模型超参数（来自 mastery_update_mechanism.md §超参数总览）。

模块级常量——可运行时覆盖：scripts 调参时用
``import service.core.constants as c; c.KAPPA = 2.0`` 改模块属性，
不要 ``from ...import KAPPA``（那样拿到的是局部变量，改了不生效）。
"""
P = 2.0                # 先验伪计数
K = 5.0                # IRT 区分度 默认值（每题可独立传入）
TAU_MIN = 7.0          # firm=0.3（最不扎实）时的遗忘时间常数（天）
TAU_MAX = 90.0         # firm=1.0（最扎实）时的遗忘时间常数（天，可调 60~180）
TAU_PEAK = 180.0       # 峰值衰减常数（天，须 >> TAU_MAX，否则 savings 缺口打不开）
KAPPA = 1.0            # 基础伪观测权重
MU = 1.0               # evidence → 掌握度位移系数（已并入 m_obs，校准可调）
RHO = 1.0              # 重学节省系数
N_MAX = 100            # 总证据量上限
K_SAT = 8              # 置信度饱和常数（仅读时派生展示用，不进更新回路）

# 固定常量（不调）
FIRM_MIN = 0.3         # firm 下限 / τ 插值起点
FIRM_RANGE = 0.7       # firm 动态幅度 / τ 插值分母（=1−FIRM_MIN）
FIRM_SAT = 5.0         # firm 证据饱和常数
M_OBS_MIN = 0.001      # m_obs 下限（防 Beta 退化）
M_OBS_MAX = 0.999      # m_obs 上限
SAVINGS_CAP = 3.0      # savings 封顶倍数
