"""mastery_store 包入口：re-export 公共 API。

原先只能在 mastery_store/ 目录内 `import mastery_store`（找到 mastery_store.py）；
加了本 __init__ 后，从仓库根 `import mastery_store` 走包，re-export 同样可用，
从而消除业务代码里的 sys.path.insert hack。
"""
from .mastery_store import (  # noqa: F401
    # 读
    get_state, get_mastery, get_confidence, get_converged_mastery,
    weakest_kps,
    # 写
    update, process_answer, init_student,
    # 算法
    forget_decay, step_update, sigmoid, clamp,
    # 常量
    P, N_MAX, KAPPA, K_SAT,
    TAU_MIN, TAU_MAX, TAU_PEAK,
    FIRM_MIN, FIRM_RANGE, FIRM_SAT,
    RHO, SAVINGS_CAP,
    # 知识点注册表
    KP_NAMES, NAME2IDX, N_KP,
    # 底层
    conn,
)
from .practice import (  # noqa: F401
    log_practice_event, get_kp_sequence, get_student_events, seen_question_ids,
    get_latest_results, find_event_by_request_id,
    get_kp_stats, get_history_summary,
)

__all__ = [
    "get_state", "get_mastery", "get_confidence", "get_converged_mastery",
    "weakest_kps", "update", "process_answer", "init_student",
    "forget_decay", "step_update", "sigmoid", "clamp",
    "P", "N_MAX", "KAPPA", "K_SAT",
    "TAU_MIN", "TAU_MAX", "TAU_PEAK",
    "FIRM_MIN", "FIRM_RANGE", "FIRM_SAT", "RHO", "SAVINGS_CAP",
    "KP_NAMES", "NAME2IDX", "N_KP", "conn",
    "log_practice_event", "get_kp_sequence", "get_student_events", "seen_question_ids",
    "get_latest_results", "find_event_by_request_id",
    "get_kp_stats", "get_history_summary",
]
