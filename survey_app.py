import time
import uuid
import random
from pathlib import Path
from datetime import datetime

import pandas as pd
import streamlit as st
from supabase import create_client


# =====================================================
# 页面基础设置
# =====================================================

st.set_page_config(
    page_title="老年生活圈服务需求与街景感知调研",
    page_icon="🌳",
    layout="wide"
)


# =====================================================
# 路径与常量
# =====================================================

BASE_DIR = Path(__file__).parent
SCENES_CSV = BASE_DIR / "scenes_1000_cloud.csv"

FORMAL_MODE = "正式调研"
TEST_MODE = "测试 / 预览"

CHOICE_LABELS = {
    "A": "左边更适合",
    "B": "右边更适合",
    "Tie": "差不多 / 看不出来"
}

REASON_OPTIONS = [
    "更安全",
    "更省力",
    "更方便",
    "更舒服",
    "设施更适老",
    "说不清，就是感觉更好"
]


# =====================================================
# Supabase 连接
# =====================================================

@st.cache_resource
def get_supabase_client():
    url = st.secrets.get("SUPABASE_URL", "")
    key = st.secrets.get("SUPABASE_KEY", "")

    if not url or not key:
        return None

    return create_client(url, key)


def require_supabase_if_formal(mode):
    if mode == FORMAL_MODE:
        supabase = get_supabase_client()
        if supabase is None:
            st.error(
                "正式调研模式需要配置 Supabase Secrets：SUPABASE_URL 和 SUPABASE_KEY。"
            )
            st.stop()
        return supabase
    return None


# =====================================================
# 数据读取
# =====================================================

@st.cache_data
def load_scenes():
    if not SCENES_CSV.exists():
        st.error(f"找不到场景文件：{SCENES_CSV}")
        st.stop()

    df = pd.read_csv(SCENES_CSV, encoding="utf-8-sig")

    if "image_url" not in df.columns:
        st.error("scenes_1000_cloud.csv 必须包含 image_url 字段。")
        st.stop()

    if "scene_id" not in df.columns:
        df["scene_id"] = [f"S{i+1:04d}" for i in range(len(df))]

    if "filename" not in df.columns:
        df["filename"] = ""

    if "OID" not in df.columns:
        df["OID"] = ""

    df["scene_id"] = df["scene_id"].astype(str)
    df["image_url"] = df["image_url"].astype(str)
    df["filename"] = df["filename"].astype(str)
    df["OID"] = df["OID"].astype(str)

    return df


SCENES = load_scenes()


# =====================================================
# 通用工具函数
# =====================================================

def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def make_respondent_id():
    t = datetime.now().strftime("%Y%m%d_%H%M%S")
    u = uuid.uuid4().hex[:8]
    return f"R_{t}_{u}"


def join_values(values):
    if values is None:
        return ""
    if isinstance(values, list):
        return "；".join([str(v) for v in values])
    return str(values)


def pair_key(scene_a, scene_b):
    a, b = sorted([str(scene_a), str(scene_b)])
    return f"{a}__{b}"


def safe_execute(func, err_msg="数据库操作失败"):
    try:
        return func()
    except Exception as e:
        st.error(f"{err_msg}：{e}")
        st.stop()


def fetch_all_rows(supabase, table_name, columns="*"):
    rows = []
    start = 0
    step = 1000

    while True:
        res = (
            supabase
            .table(table_name)
            .select(columns)
            .range(start, start + step - 1)
            .execute()
        )

        data = res.data or []
        rows.extend(data)

        if len(data) < step:
            break

        start += step

    return rows


def insert_rows(supabase, table_name, rows, chunk_size=500):
    if not rows:
        return

    for i in range(0, len(rows), chunk_size):
        chunk = rows[i:i + chunk_size]
        supabase.table(table_name).insert(chunk).execute()


# =====================================================
# Supabase 写入函数
# =====================================================

def save_respondent_to_supabase(row):
    supabase = get_supabase_client()
    supabase.table("respondents").upsert(row).execute()


def save_choice_to_supabase(row):
    supabase = get_supabase_client()
    supabase.table("streetview_choices").insert(row).execute()


def get_scene_counts_from_supabase():
    supabase = get_supabase_client()
    rows = fetch_all_rows(supabase, "scene_appear_count", "scene_id,appear_count")

    counts = {sid: 0 for sid in SCENES["scene_id"].astype(str).tolist()}

    for r in rows:
        sid = str(r.get("scene_id", ""))
        counts[sid] = int(r.get("appear_count") or 0)

    return counts


def get_existing_pair_keys_from_supabase():
    supabase = get_supabase_client()
    rows = fetch_all_rows(supabase, "pair_log", "pair_key")
    return set(str(r.get("pair_key", "")) for r in rows if r.get("pair_key"))


def commit_generated_pairs_to_supabase(respondent_id, pairs, updated_counts):
    supabase = get_supabase_client()
    ts = now_text()

    pair_rows = []
    question_rows = []

    for p in pairs:
        pair_rows.append({
            "pair_key": p["pair_key"],
            "scene_a": p["scene_a"],
            "scene_b": p["scene_b"],
            "respondent_id": respondent_id,
            "timestamp": ts
        })

        question_rows.append({
            "respondent_id": respondent_id,
            "pair_id": p["pair_id"],
            "scene_a": p["scene_a"],
            "scene_b": p["scene_b"],
            "pair_key": p["pair_key"],
            "ask_reason": int(p["ask_reason"]),
            "timestamp": ts
        })

    insert_rows(supabase, "pair_log", pair_rows)
    insert_rows(supabase, "question_log", question_rows)

    used_scene_ids = set()
    for p in pairs:
        used_scene_ids.add(p["scene_a"])
        used_scene_ids.add(p["scene_b"])

    count_rows = [
        {
            "scene_id": sid,
            "appear_count": int(updated_counts.get(sid, 0))
        }
        for sid in used_scene_ids
    ]

    if count_rows:
        supabase.table("scene_appear_count").upsert(
            count_rows,
            on_conflict="scene_id"
        ).execute()


# =====================================================
# 抽题逻辑
# =====================================================

def generate_pairs(
    respondent_id,
    n_questions,
    reason_ratio,
    mode
):
    all_scene_ids = SCENES["scene_id"].astype(str).tolist()

    if mode == FORMAL_MODE:
        counts = get_scene_counts_from_supabase()
        existing_pairs = get_existing_pair_keys_from_supabase()
    else:
        counts = {sid: 0 for sid in all_scene_ids}
        existing_pairs = set()

    local_pairs = set()
    generated = []

    reason_n = int(round(n_questions * reason_ratio))
    reason_indices = set(random.sample(range(n_questions), reason_n)) if reason_n > 0 else set()

    max_attempts = n_questions * 300
    attempts = 0

    while len(generated) < n_questions and attempts < max_attempts:
        attempts += 1

        sorted_ids = sorted(
            all_scene_ids,
            key=lambda sid: (counts.get(sid, 0), random.random())
        )

        pool_size = max(50, min(len(sorted_ids), int(len(sorted_ids) * 0.25)))
        candidate_pool = sorted_ids[:pool_size]

        a, b = random.sample(candidate_pool, 2)
        pk = pair_key(a, b)

        if pk in local_pairs:
            continue

        if mode == FORMAL_MODE and pk in existing_pairs:
            continue

        # 左右随机
        if random.random() < 0.5:
            scene_a, scene_b = a, b
        else:
            scene_a, scene_b = b, a

        trial_index = len(generated) + 1

        p = {
            "trial_index": trial_index,
            "pair_id": f"{respondent_id}_P{trial_index:03d}",
            "scene_a": scene_a,
            "scene_b": scene_b,
            "pair_key": pk,
            "ask_reason": 1 if (trial_index - 1) in reason_indices else 0
        }

        generated.append(p)
        local_pairs.add(pk)
        existing_pairs.add(pk)

        counts[scene_a] = int(counts.get(scene_a, 0)) + 1
        counts[scene_b] = int(counts.get(scene_b, 0)) + 1

    if len(generated) < n_questions:
        st.warning(
            f"原计划生成 {n_questions} 道街景题，但只生成了 {len(generated)} 道。"
        )

    if mode == FORMAL_MODE:
        commit_generated_pairs_to_supabase(respondent_id, generated, counts)

    return generated


# =====================================================
# 页面状态初始化
# =====================================================

if "step" not in st.session_state:
    st.session_state.step = "basic"

if "respondent_id" not in st.session_state:
    st.session_state.respondent_id = make_respondent_id()

if "pairs" not in st.session_state:
    st.session_state.pairs = []

if "current_trial" not in st.session_state:
    st.session_state.current_trial = 0

if "trial_start_time" not in st.session_state:
    st.session_state.trial_start_time = time.time()

if "pending_choice" not in st.session_state:
    st.session_state.pending_choice = None

if "pending_choice_label" not in st.session_state:
    st.session_state.pending_choice_label = None

if "basic_saved" not in st.session_state:
    st.session_state.basic_saved = False


# =====================================================
# 标题
# =====================================================

st.title("老年生活圈服务需求与街景感知可达性调研问卷")

st.markdown(
    """
本调研仅用于学术研究与社区服务优化，所有信息匿名处理。  
填写对象：**60周岁及以上老年居民**。
"""
)


# =====================================================
# 侧边栏设置
# =====================================================

st.sidebar.header("调研设置")

mode = st.sidebar.radio(
    "调研模式",
    [TEST_MODE, FORMAL_MODE],
    index=0,
    help="测试 / 预览模式不会写入 Supabase 数据库。正式调研模式会保存数据。"
)

is_test_value = "测试" if mode == TEST_MODE else "正式"

interviewer_id = st.sidebar.text_input(
    "调研员编号",
    value="",
    placeholder="例如 A01"
)

community = st.sidebar.text_input(
    "社区名称",
    value="",
    placeholder="例如 XX社区"
)

n_questions = st.sidebar.selectbox(
    "街景比较题数量",
    [40, 60, 80, 100],
    index=1
)

reason_ratio_percent = st.sidebar.selectbox(
    "原因追问比例",
    [0, 10, 15, 20],
    index=3
)

reason_ratio = reason_ratio_percent / 100

st.sidebar.caption(f"当前受访者ID：{st.session_state.respondent_id}")

if mode == TEST_MODE:
    st.sidebar.warning("当前为测试 / 预览模式，不会保存到正式数据库。")
else:
    st.sidebar.success("当前为正式调研模式，数据会写入 Supabase。")


# =====================================================
# 问卷表单
# =====================================================

def basic_form_page():
    st.subheader("B1. 基本信息与生活状态")

    with st.form("basic_questionnaire_form"):

        q1_age = st.radio(
            "**1. 年龄**",
            ["60—69岁", "70—79岁", "80岁及以上"],
            index=None
        )

        q2_gender = st.radio(
            "**2. 性别**",
            ["男", "女"],
            index=None
        )

        q3_health = st.radio(
            "**3. 身体健康状况自评**",
            ["很健康", "较健康", "一般", "较差", "很差"],
            index=None
        )

        q4_living = st.radio(
            "**4. 目前主要与谁一起居住**",
            ["独居", "与配偶同住", "与子女同住", "与亲友/保姆同住", "养老机构/照护机构"],
            index=None
        )

        q5_care = st.radio(
            "**5. 平时是否需要其他人照顾**",
            ["完全依赖他人照护", "部分需要辅助", "一般", "不太需要", "完全自理"],
            index=None
        )

        q6_independent_outing = st.radio(
            "**6. 是否能独立外出**",
            ["可以独立外出", "可以外出但需要陪同或帮助", "很少外出", "基本不外出"],
            index=None
        )

        q7_assistive_tool = st.radio(
            "**7. 外出时是否使用辅助工具**",
            ["不使用", "拐杖", "助行器", "轮椅", "其他"],
            index=None
        )

        q7_assistive_tool_other = ""
        if q7_assistive_tool == "其他":
            q7_assistive_tool_other = st.text_input("请填写其他辅助工具")

        st.subheader("B2. 设施需求情况")

        need_options = ["非常需要", "比较需要", "一般", "不太需要", "不需要"]

        q8_medical_need = st.radio(
            "**8. 平时常去社区卫生站或者附近医院看病吗？**",
            need_options,
            index=None
        )

        st.markdown("**9. 是否需要养老或照护服务？**")

        q9_day_care = st.radio("日间照料", need_options, index=None, horizontal=True)
        q9_bathing = st.radio("助浴", need_options, index=None, horizontal=True)
        q9_cleaning = st.radio("助洁", need_options, index=None, horizontal=True)
        q9_rehab_nursing = st.radio("康复护理", need_options, index=None, horizontal=True)
        q9_medical_accompany = st.radio("助医/陪诊", need_options, index=None, horizontal=True)

        q10_meal_service = st.radio(
            "**10. 是否需要社区食堂或助餐服务？**",
            need_options,
            index=None
        )

        q11_recreation_facility = st.radio(
            "**11. 是否使用社区文娱或休闲活动设施？**",
            need_options,
            index=None
        )

        q12_public_transit = st.radio(
            "**12. 平时经常坐公交或轨道交通吗？**",
            need_options,
            index=None
        )

        q13_lacking_facilities = st.multiselect(
            "**13. 社区最缺或问题最大的设施，最多选3项**",
            ["医疗设施", "养老/康养设施", "餐饮设施", "文娱设施", "交通设施", "暂时没有明显缺口", "其他"]
        )

        q13_lacking_facilities_other = ""
        if "其他" in q13_lacking_facilities:
            q13_lacking_facilities_other = st.text_input("请填写其他缺口设施")

        st.subheader("C. 山地步行与街道环境敏感性")

        q14_park_green_freq = st.radio(
            "**14. 去公园/广场/公共绿地频率**",
            ["几乎每天", "每周3—5次", "每周1—2次", "偶尔去", "基本不去"],
            index=None
        )

        q15_acceptable_walk_time = st.radio(
            "**15. 最多能接受走多久？**",
            ["5分钟内", "10分钟内", "15分钟内", "30分钟内", "大于30分钟", "平时不去", "其他"],
            index=None
        )

        q15_acceptable_walk_time_other = ""
        if q15_acceptable_walk_time == "其他":
            q15_acceptable_walk_time_other = st.text_input("请填写可接受步行时间")

        q16_alternative_activity_places = st.multiselect(
            "**16. 如果距离公园/广场较远，去哪里户外活动？可多选**",
            [
                "小区楼下",
                "小区内部活动空间",
                "街边座椅/树荫处",
                "社区广场",
                "社区服务中心附近",
                "菜市场/商店附近",
                "不进行户外活动",
                "其他"
            ]
        )

        q16_alternative_activity_places_other = ""
        if "其他" in q16_alternative_activity_places:
            q16_alternative_activity_places_other = st.text_input("请填写其他户外活动地点")

        q17_slope_step_feeling = st.radio(
            "**17. “走5分钟连续上坡或台阶路”，体感相当于平路多久？**",
            ["差不多5分钟", "像走了8分钟", "像走了10分钟", "像走了15分钟以上", "说不清"],
            index=None
        )

        q18_rest_interval = st.radio(
            "**18. 连续步行多久需要休息一次？**",
            ["5分钟以内", "5—10分钟", "10—15分钟", "15—30分钟", "30分钟以上", "不确定"],
            index=None
        )

        reduce_options = ["明显会", "有些会", "一般", "不太会", "不会"]

        st.markdown("**19. 以下场景是否会减少外出步行意愿？**")

        q19_slope_reduce = st.radio("连续上坡/坡度较大", reduce_options, index=None, horizontal=True)
        q19_steps_reduce = st.radio("上下台阶多", reduce_options, index=None, horizontal=True)
        q19_sidewalk_reduce = st.radio("人行道不连续、较窄", reduce_options, index=None, horizontal=True)
        q19_traffic_reduce = st.radio("车流较多", reduce_options, index=None, horizontal=True)

        increase_options = ["明显会", "有些会", "一般", "不太会", "不会"]

        st.markdown("**20. 以下场景是否会增加外出步行意愿？**")

        q20_seat_increase = st.radio("步行道路有休憩座椅", increase_options, index=None, horizontal=True)
        q20_shade_increase = st.radio("步行道路有树荫或遮阴", increase_options, index=None, horizontal=True)
        q20_handrail_increase = st.radio("台阶或坡道旁有扶手", increase_options, index=None, horizontal=True)
        q20_lively_shop_increase = st.radio("路上比较热闹、有商店或服务点", increase_options, index=None, horizontal=True)

        q21_open_environment = st.text_area(
            "**21. 什么样的街道环境会想让您外出，或什么样的街道环境会让您不想外出？可选填。**"
        )

        st.markdown("---")
        st.markdown("### 填完问卷后请选择")

        submit_end = st.form_submit_button("提交问卷并结束", use_container_width=True)
        submit_continue = st.form_submit_button("提交问卷并继续街景比较", use_container_width=True)

    if submit_end or submit_continue:
        required_items = {
            "1. 年龄": q1_age,
            "2. 性别": q2_gender,
            "3. 健康状况": q3_health,
            "4. 居住情况": q4_living,
            "5. 照护需求": q5_care,
            "6. 独立外出": q6_independent_outing,
            "7. 辅助工具": q7_assistive_tool,
            "8. 医疗需求": q8_medical_need,
            "9. 日间照料": q9_day_care,
            "9. 助浴": q9_bathing,
            "9. 助洁": q9_cleaning,
            "9. 康复护理": q9_rehab_nursing,
            "9. 助医/陪诊": q9_medical_accompany,
            "10. 助餐服务": q10_meal_service,
            "11. 文娱休闲": q11_recreation_facility,
            "12. 公共交通": q12_public_transit,
            "14. 公园绿地频率": q14_park_green_freq,
            "15. 可接受步行时间": q15_acceptable_walk_time,
            "17. 坡道台阶体感": q17_slope_step_feeling,
            "18. 休息间隔": q18_rest_interval,
            "19. 连续上坡": q19_slope_reduce,
            "19. 台阶多": q19_steps_reduce,
            "19. 人行道问题": q19_sidewalk_reduce,
            "19. 车流较多": q19_traffic_reduce,
            "20. 座椅": q20_seat_increase,
            "20. 树荫": q20_shade_increase,
            "20. 扶手": q20_handrail_increase,
            "20. 热闹商店": q20_lively_shop_increase,
        }

        missing = [k for k, v in required_items.items() if v is None or v == ""]

        if len(q13_lacking_facilities) > 3:
            st.error("第13题最多选择3项。")
            return

        if missing:
            st.error("以下必答题还没有填写：")
            for m in missing:
                st.write(f"- {m}")
            return

        respondent_row = {
            "respondent_id": st.session_state.respondent_id,
            "is_test": is_test_value,
            "interviewer_id": interviewer_id,
            "community": community,
            "random_seed": str(random.random()),
            "n_questions": str(n_questions),
            "reason_ratio": str(reason_ratio_percent),

            "q1_age": q1_age,
            "q2_gender": q2_gender,
            "q3_health": q3_health,
            "q4_living": q4_living,
            "q5_care": q5_care,
            "q6_independent_outing": q6_independent_outing,
            "q7_assistive_tool": q7_assistive_tool,
            "q7_assistive_tool_other": q7_assistive_tool_other,

            "q8_medical_need": q8_medical_need,
            "q9_day_care": q9_day_care,
            "q9_bathing": q9_bathing,
            "q9_cleaning": q9_cleaning,
            "q9_rehab_nursing": q9_rehab_nursing,
            "q9_medical_accompany": q9_medical_accompany,
            "q10_meal_service": q10_meal_service,
            "q11_recreation_facility": q11_recreation_facility,
            "q12_public_transit": q12_public_transit,
            "q13_lacking_facilities": join_values(q13_lacking_facilities),
            "q13_lacking_facilities_other": q13_lacking_facilities_other,

            "q14_park_green_freq": q14_park_green_freq,
            "q15_acceptable_walk_time": q15_acceptable_walk_time,
            "q15_acceptable_walk_time_other": q15_acceptable_walk_time_other,
            "q16_alternative_activity_places": join_values(q16_alternative_activity_places),
            "q16_alternative_activity_places_other": q16_alternative_activity_places_other,
            "q17_slope_step_feeling": q17_slope_step_feeling,
            "q18_rest_interval": q18_rest_interval,

            "q19_slope_reduce": q19_slope_reduce,
            "q19_steps_reduce": q19_steps_reduce,
            "q19_sidewalk_reduce": q19_sidewalk_reduce,
            "q19_traffic_reduce": q19_traffic_reduce,

            "q20_seat_increase": q20_seat_increase,
            "q20_shade_increase": q20_shade_increase,
            "q20_handrail_increase": q20_handrail_increase,
            "q20_lively_shop_increase": q20_lively_shop_increase,

            "q21_open_environment": q21_open_environment,
            "submit_basic_time": now_text()
        }

        if mode == FORMAL_MODE:
            require_supabase_if_formal(mode)
            safe_execute(
                lambda: save_respondent_to_supabase(respondent_row),
                "保存问卷基本信息失败"
            )
            st.session_state.basic_saved = True
        else:
            st.session_state.basic_saved = True

        if submit_end:
            st.session_state.step = "finished_basic_only"
            st.rerun()

        if submit_continue:
            with st.spinner("正在生成街景比较题，请稍候..."):
                require_supabase_if_formal(mode)
                pairs = generate_pairs(
                    respondent_id=st.session_state.respondent_id,
                    n_questions=n_questions,
                    reason_ratio=reason_ratio,
                    mode=mode
                )

            st.session_state.pairs = pairs
            st.session_state.current_trial = 0
            st.session_state.trial_start_time = time.time()
            st.session_state.pending_choice = None
            st.session_state.pending_choice_label = None
            st.session_state.step = "streetview"
            st.rerun()


# =====================================================
# 街景比较页面
# =====================================================

def get_scene_row(scene_id):
    df = SCENES[SCENES["scene_id"].astype(str) == str(scene_id)]
    if df.empty:
        return None
    return df.iloc[0]


def save_current_choice(choice, choice_label, reasons=None):
    idx = st.session_state.current_trial
    pair = st.session_state.pairs[idx]

    response_time = time.time() - st.session_state.trial_start_time

    row = {
        "respondent_id": st.session_state.respondent_id,
        "is_test": is_test_value,
        "interviewer_id": interviewer_id,
        "community": community,
        "random_seed": "",
        "n_questions": str(len(st.session_state.pairs)),
        "reason_ratio": str(reason_ratio_percent),

        "trial_index": int(pair["trial_index"]),
        "pair_id": pair["pair_id"],
        "scene_a": pair["scene_a"],
        "scene_b": pair["scene_b"],
        "pair_key": pair["pair_key"],

        "choice": choice,
        "choice_label": choice_label,
        "ask_reason": int(pair["ask_reason"]),
        "reasons": join_values(reasons or []),
        "response_time": float(response_time),
        "timestamp": now_text()
    }

    if mode == FORMAL_MODE:
        safe_execute(
            lambda: save_choice_to_supabase(row),
            "保存街景比较结果失败"
        )

    st.session_state.current_trial += 1
    st.session_state.pending_choice = None
    st.session_state.pending_choice_label = None
    st.session_state.trial_start_time = time.time()

    if st.session_state.current_trial >= len(st.session_state.pairs):
        st.session_state.step = "finished_all"

    st.rerun()


def streetview_page():
    st.subheader("街景成对比较")

    total = len(st.session_state.pairs)
    idx = st.session_state.current_trial

    if total == 0:
        st.warning("没有生成街景题。")
        if st.button("结束"):
            st.session_state.step = "finished_basic_only"
            st.rerun()
        return

    if idx >= total:
        st.session_state.step = "finished_all"
        st.rerun()

    pair = st.session_state.pairs[idx]

    progress = (idx + 1) / total
    st.progress(progress)
    st.markdown(f"### 第 {idx + 1} / {total} 题")

    st.markdown(
        "**如果您要步行去附近公园、广场或公共绿地，您觉得哪一个街道场景整体更适合老年人走？**"
    )

    scene_a = get_scene_row(pair["scene_a"])
    scene_b = get_scene_row(pair["scene_b"])

    if scene_a is None or scene_b is None:
        st.error("场景信息缺失。")
        return

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("#### 左边场景")
        st.image(scene_a["image_url"], use_container_width=True)

    with col2:
        st.markdown("#### 右边场景")
        st.image(scene_b["image_url"], use_container_width=True)

    st.markdown("---")

    if pair["ask_reason"] == 1 and st.session_state.pending_choice is not None:
        st.info(f"您刚才选择了：{st.session_state.pending_choice_label}")
        reasons = st.multiselect(
            "请问主要原因是什么？可多选",
            REASON_OPTIONS
        )

        col_confirm, col_back = st.columns([1, 1])

        with col_confirm:
            if st.button("确认并进入下一题", use_container_width=True):
                save_current_choice(
                    st.session_state.pending_choice,
                    st.session_state.pending_choice_label,
                    reasons
                )

        with col_back:
            if st.button("返回重新选择", use_container_width=True):
                st.session_state.pending_choice = None
                st.session_state.pending_choice_label = None
                st.rerun()

    else:
        c1, c2, c3 = st.columns(3)

        with c1:
            if st.button("左边更适合", use_container_width=True):
                if pair["ask_reason"] == 1:
                    st.session_state.pending_choice = "A"
                    st.session_state.pending_choice_label = CHOICE_LABELS["A"]
                    st.rerun()
                else:
                    save_current_choice("A", CHOICE_LABELS["A"])

        with c2:
            if st.button("右边更适合", use_container_width=True):
                if pair["ask_reason"] == 1:
                    st.session_state.pending_choice = "B"
                    st.session_state.pending_choice_label = CHOICE_LABELS["B"]
                    st.rerun()
                else:
                    save_current_choice("B", CHOICE_LABELS["B"])

        with c3:
            if st.button("差不多 / 看不出来", use_container_width=True):
                if pair["ask_reason"] == 1:
                    st.session_state.pending_choice = "Tie"
                    st.session_state.pending_choice_label = CHOICE_LABELS["Tie"]
                    st.rerun()
                else:
                    save_current_choice("Tie", CHOICE_LABELS["Tie"])

    st.markdown("---")

    if st.button("结束街景比较", type="secondary"):
        st.session_state.step = "finished_early"
        st.rerun()


# =====================================================
# 结束页面
# =====================================================

def finished_page(message):
    st.success(message)

    if mode == TEST_MODE:
        st.info("当前为测试 / 预览模式，本次填写内容没有写入正式数据库。")
    else:
        st.info("数据已保存到 Supabase 云数据库。")

    st.markdown("感谢您的参与！")

    if st.button("开始下一份问卷"):
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        st.rerun()


# =====================================================
# 主流程
# =====================================================

if st.session_state.step == "basic":
    basic_form_page()

elif st.session_state.step == "streetview":
    streetview_page()

elif st.session_state.step == "finished_basic_only":
    finished_page("问卷已提交。")

elif st.session_state.step == "finished_all":
    finished_page("问卷与街景比较均已完成。")

elif st.session_state.step == "finished_early":
    finished_page("已提前结束街景比较，已完成的部分已保存。")