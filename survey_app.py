import time
import uuid
import random
from pathlib import Path
from datetime import datetime

import pandas as pd
import streamlit as st
from supabase import create_client


# =====================================================
# 页面设置
# =====================================================

st.set_page_config(
    page_title="老年生活圈服务需求与街景感知调研",
    page_icon="🌳",
    layout="centered",
    initial_sidebar_state="collapsed"
)


# =====================================================
# 基础常量
# =====================================================

BASE_DIR = Path(__file__).parent
SCENES_CSV = BASE_DIR / "scenes_1000_cloud.csv"

FORMAL_MODE = "正式调研"
TEST_MODE = "测试 / 预览"

NEED_OPTIONS = ["非常需要", "比较需要", "一般", "不太需要", "不需要"]
REDUCE_OPTIONS = ["明显会", "有些会", "一般", "不太会", "不会"]

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
# 工具函数
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


def rerun():
    st.rerun()


def go_to(step):
    st.session_state.step = step
    rerun()


def reset_all():
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    rerun()


def init_state():
    defaults = {
        "step": "intro",
        "respondent_id": make_respondent_id(),
        "answers": {},
        "pairs": [],
        "current_trial": 0,
        "trial_start_time": time.time(),
        "pending_choice": None,
        "pending_choice_label": None,
        "survey_meta": {},
        "basic_saved": False
    }

    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


init_state()


# =====================================================
# 延迟加载 scenes：只有进入街景时才读 CSV
# =====================================================

@st.cache_data(show_spinner=False)
def load_scenes():
    if not SCENES_CSV.exists():
        st.error(f"找不到场景文件：{SCENES_CSV}")
        st.stop()

    df = pd.read_csv(SCENES_CSV, encoding="utf-8-sig")

    if "image_url" not in df.columns:
        st.error("scenes_1000_cloud.csv 必须包含 image_url 字段。")
        st.stop()

    if "scene_id" not in df.columns:
        df["scene_id"] = [f"S{i + 1:04d}" for i in range(len(df))]

    if "filename" not in df.columns:
        df["filename"] = ""

    if "OID" not in df.columns:
        df["OID"] = ""

    df["scene_id"] = df["scene_id"].astype(str)
    df["image_url"] = df["image_url"].astype(str)
    df["filename"] = df["filename"].astype(str)
    df["OID"] = df["OID"].astype(str)

    return df


def get_scene_row(scene_id):
    scenes = load_scenes()
    matched = scenes[scenes["scene_id"].astype(str) == str(scene_id)]

    if matched.empty:
        return None

    return matched.iloc[0]


# =====================================================
# Supabase：只有正式调研需要时才连接
# =====================================================

@st.cache_resource(show_spinner=False)
def get_supabase_client():
    try:
        url = st.secrets["SUPABASE_URL"]
        key = st.secrets["SUPABASE_KEY"]
    except Exception:
        st.error(
            "正式调研模式需要配置 Supabase Secrets：SUPABASE_URL 和 SUPABASE_KEY。"
        )
        st.stop()

    return create_client(url, key)


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


def save_respondent_to_supabase(row):
    supabase = get_supabase_client()
    supabase.table("respondents").upsert(row).execute()


def save_choice_to_supabase(row):
    supabase = get_supabase_client()
    supabase.table("streetview_choices").insert(row).execute()


def get_scene_counts_from_supabase():
    scenes = load_scenes()
    scene_ids = scenes["scene_id"].astype(str).tolist()

    counts = {sid: 0 for sid in scene_ids}

    supabase = get_supabase_client()
    rows = fetch_all_rows(
        supabase,
        "scene_appear_count",
        "scene_id,appear_count"
    )

    for r in rows:
        sid = str(r.get("scene_id", ""))
        counts[sid] = int(r.get("appear_count") or 0)

    return counts


def get_existing_pair_keys_from_supabase():
    supabase = get_supabase_client()
    rows = fetch_all_rows(supabase, "pair_log", "pair_key")
    return set(str(r.get("pair_key", "")) for r in rows if r.get("pair_key"))


def commit_generated_pairs_to_supabase(respondent_id, pairs, updated_counts):
    if not pairs:
        return

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
# 生成街景 pair
# =====================================================

def generate_pairs(respondent_id, n_questions, reason_ratio, is_formal):
    scenes = load_scenes()
    all_scene_ids = scenes["scene_id"].astype(str).tolist()

    if is_formal:
        counts = get_scene_counts_from_supabase()
        existing_pairs = get_existing_pair_keys_from_supabase()
    else:
        counts = {sid: 0 for sid in all_scene_ids}
        existing_pairs = set()

    local_pairs = set()
    generated = []

    reason_n = int(round(n_questions * reason_ratio))
    if reason_n > 0:
        reason_indices = set(random.sample(range(n_questions), reason_n))
    else:
        reason_indices = set()

    max_attempts = n_questions * 500
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

        if is_formal and pk in existing_pairs:
            continue

        if random.random() < 0.5:
            scene_a, scene_b = a, b
        else:
            scene_a, scene_b = b, a

        trial_index = len(generated) + 1

        generated.append({
            "trial_index": trial_index,
            "pair_id": f"{respondent_id}_P{trial_index:03d}",
            "scene_a": scene_a,
            "scene_b": scene_b,
            "pair_key": pk,
            "ask_reason": 1 if (trial_index - 1) in reason_indices else 0
        })

        local_pairs.add(pk)
        existing_pairs.add(pk)

        counts[scene_a] = int(counts.get(scene_a, 0)) + 1
        counts[scene_b] = int(counts.get(scene_b, 0)) + 1

    if is_formal:
        commit_generated_pairs_to_supabase(
            respondent_id,
            generated,
            counts
        )

    return generated


# =====================================================
# 页面通用头部
# =====================================================

def page_header():
    st.title("老年生活圈服务需求与街景感知可达性调研问卷")

    st.caption(
        "本调研仅用于学术研究与社区服务优化，所有信息匿名处理。填写对象：60周岁及以上老年居民。"
    )


def sidebar_settings():
    st.sidebar.header("调研设置")

    mode = st.sidebar.radio(
        "调研模式",
        [TEST_MODE, FORMAL_MODE],
        index=0
    )

    interviewer_id = st.sidebar.text_input(
        "调研员编号",
        value=st.session_state.survey_meta.get("interviewer_id", "")
    )

    community = st.sidebar.text_input(
        "社区名称",
        value=st.session_state.survey_meta.get("community", "")
    )

    n_questions = st.sidebar.selectbox(
        "街景比较题数量",
        [40, 60, 80, 100],
        index=[40, 60, 80, 100].index(
            int(st.session_state.survey_meta.get("n_questions", 60))
        ) if st.session_state.survey_meta.get("n_questions") else 1
    )

    reason_ratio_percent = st.sidebar.selectbox(
        "原因追问比例",
        [0, 10, 15, 20],
        index=[0, 10, 15, 20].index(
            int(st.session_state.survey_meta.get("reason_ratio_percent", 20))
        ) if st.session_state.survey_meta.get("reason_ratio_percent") else 3
    )

    is_formal = mode == FORMAL_MODE

    st.session_state.survey_meta.update({
        "mode": mode,
        "is_test": "正式" if is_formal else "测试",
        "interviewer_id": interviewer_id,
        "community": community,
        "n_questions": n_questions,
        "reason_ratio_percent": reason_ratio_percent,
        "reason_ratio": reason_ratio_percent / 100,
        "is_formal": is_formal
    })

    if is_formal:
        st.sidebar.success("正式调研模式：提交后写入 Supabase")
    else:
        st.sidebar.warning("测试 / 预览模式：不写入正式数据库")

    st.sidebar.caption(f"受访者ID：{st.session_state.respondent_id}")


# =====================================================
# 首页
# =====================================================

def intro_page():
    page_header()
    sidebar_settings()

    st.markdown("### 调研流程")

    st.markdown(
        """
1. 填写基本信息与生活状态  
2. 填写设施需求情况  
3. 填写山地步行与街道环境敏感性  
4. 可选择只提交问卷，或继续进行街景比较  
"""
    )

    meta = st.session_state.survey_meta

    if meta["is_formal"]:
        st.info("当前为正式调研模式。提交后数据会写入云端数据库。")
    else:
        st.info("当前为测试 / 预览模式。可以给他人查看问卷内容，不会写入正式数据库。")

    if st.button("开始填写问卷", use_container_width=True):
        go_to("b1")


# =====================================================
# B1 页面
# =====================================================

def b1_page():
    page_header()
    sidebar_settings()

    a = st.session_state.answers

    st.subheader("B1. 基本信息与生活状态")

    with st.form("b1_form"):
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

        submitted = st.form_submit_button("下一页：设施需求情况", use_container_width=True)

    if submitted:
        required = {
            "1. 年龄": q1_age,
            "2. 性别": q2_gender,
            "3. 健康状况": q3_health,
            "4. 居住情况": q4_living,
            "5. 照护需求": q5_care,
            "6. 独立外出": q6_independent_outing,
            "7. 辅助工具": q7_assistive_tool,
        }

        missing = [k for k, v in required.items() if v is None or v == ""]

        if missing:
            st.error("以下题目未填写：" + "，".join(missing))
            return

        a.update({
            "q1_age": q1_age,
            "q2_gender": q2_gender,
            "q3_health": q3_health,
            "q4_living": q4_living,
            "q5_care": q5_care,
            "q6_independent_outing": q6_independent_outing,
            "q7_assistive_tool": q7_assistive_tool,
            "q7_assistive_tool_other": q7_assistive_tool_other,
        })

        go_to("b2")


# =====================================================
# B2 页面
# =====================================================

def b2_page():
    page_header()
    sidebar_settings()

    a = st.session_state.answers

    st.subheader("B2. 设施需求情况")

    with st.form("b2_form"):
        q8_medical_need = st.radio(
            "**8. 平时常去社区卫生站或者附近医院看病吗？**",
            NEED_OPTIONS,
            index=None
        )

        st.markdown("**9. 是否需要养老或照护服务？**")

        q9_day_care = st.radio("日间照料", NEED_OPTIONS, index=None, horizontal=True)
        q9_bathing = st.radio("助浴", NEED_OPTIONS, index=None, horizontal=True)
        q9_cleaning = st.radio("助洁", NEED_OPTIONS, index=None, horizontal=True)
        q9_rehab_nursing = st.radio("康复护理", NEED_OPTIONS, index=None, horizontal=True)
        q9_medical_accompany = st.radio("助医/陪诊", NEED_OPTIONS, index=None, horizontal=True)

        q10_meal_service = st.radio(
            "**10. 是否需要社区食堂或助餐服务？**",
            NEED_OPTIONS,
            index=None
        )

        q11_recreation_facility = st.radio(
            "**11. 是否使用社区文娱或休闲活动设施？**",
            NEED_OPTIONS,
            index=None
        )

        q12_public_transit = st.radio(
            "**12. 平时经常坐公交或轨道交通吗？**",
            NEED_OPTIONS,
            index=None
        )

        q13_lacking_facilities = st.multiselect(
            "**13. 社区最缺或问题最大的设施，最多选3项**",
            ["医疗设施", "养老/康养设施", "餐饮设施", "文娱设施", "交通设施", "暂时没有明显缺口", "其他"]
        )

        q13_lacking_facilities_other = ""
        if "其他" in q13_lacking_facilities:
            q13_lacking_facilities_other = st.text_input("请填写其他缺口设施")

        c1, c2 = st.columns(2)
        with c1:
            back = st.form_submit_button("返回上一页", use_container_width=True)
        with c2:
            submitted = st.form_submit_button("下一页：山地步行情况", use_container_width=True)

    if back:
        go_to("b1")

    if submitted:
        required = {
            "8. 医疗需求": q8_medical_need,
            "9. 日间照料": q9_day_care,
            "9. 助浴": q9_bathing,
            "9. 助洁": q9_cleaning,
            "9. 康复护理": q9_rehab_nursing,
            "9. 助医/陪诊": q9_medical_accompany,
            "10. 助餐服务": q10_meal_service,
            "11. 文娱休闲": q11_recreation_facility,
            "12. 公共交通": q12_public_transit,
        }

        missing = [k for k, v in required.items() if v is None or v == ""]

        if len(q13_lacking_facilities) > 3:
            st.error("第13题最多选择3项。")
            return

        if missing:
            st.error("以下题目未填写：" + "，".join(missing))
            return

        a.update({
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
        })

        go_to("c")


# =====================================================
# C 页面
# =====================================================

def c_page():
    page_header()
    sidebar_settings()

    a = st.session_state.answers

    st.subheader("C. 山地步行与街道环境敏感性")

    with st.form("c_form"):
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

        st.markdown("**19. 以下场景是否会减少外出步行意愿？**")

        q19_slope_reduce = st.radio("连续上坡/坡度较大", REDUCE_OPTIONS, index=None, horizontal=True)
        q19_steps_reduce = st.radio("上下台阶多", REDUCE_OPTIONS, index=None, horizontal=True)
        q19_sidewalk_reduce = st.radio("人行道不连续、较窄", REDUCE_OPTIONS, index=None, horizontal=True)
        q19_traffic_reduce = st.radio("车流较多", REDUCE_OPTIONS, index=None, horizontal=True)

        st.markdown("**20. 以下场景是否会增加外出步行意愿？**")

        q20_seat_increase = st.radio("步行道路有休憩座椅", REDUCE_OPTIONS, index=None, horizontal=True)
        q20_shade_increase = st.radio("步行道路有树荫或遮阴", REDUCE_OPTIONS, index=None, horizontal=True)
        q20_handrail_increase = st.radio("台阶或坡道旁有扶手", REDUCE_OPTIONS, index=None, horizontal=True)
        q20_lively_shop_increase = st.radio("路上比较热闹、有商店或服务点", REDUCE_OPTIONS, index=None, horizontal=True)

        q21_open_environment = st.text_area(
            "**21. 什么样的街道环境会想让您外出，或什么样的街道环境会让您不想外出？可选填。**"
        )

        c1, c2 = st.columns(2)
        with c1:
            back = st.form_submit_button("返回上一页", use_container_width=True)
        with c2:
            submitted = st.form_submit_button("下一页：提交选择", use_container_width=True)

    if back:
        go_to("b2")

    if submitted:
        required = {
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

        missing = [k for k, v in required.items() if v is None or v == ""]

        if missing:
            st.error("以下题目未填写：" + "，".join(missing))
            return

        a.update({
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
        })

        go_to("submit_choice")


# =====================================================
# 保存问卷
# =====================================================

def build_respondent_row():
    a = st.session_state.answers
    meta = st.session_state.survey_meta

    row = {
        "respondent_id": st.session_state.respondent_id,
        "is_test": meta.get("is_test", "测试"),
        "interviewer_id": meta.get("interviewer_id", ""),
        "community": meta.get("community", ""),
        "random_seed": str(random.random()),
        "n_questions": str(meta.get("n_questions", "")),
        "reason_ratio": str(meta.get("reason_ratio_percent", "")),
        "submit_basic_time": now_text()
    }

    row.update(a)

    return row


def save_basic_if_needed():
    if st.session_state.basic_saved:
        return

    meta = st.session_state.survey_meta

    if meta.get("is_formal", False):
        row = build_respondent_row()
        save_respondent_to_supabase(row)

    st.session_state.basic_saved = True


# =====================================================
# 提交选择页面
# =====================================================

def submit_choice_page():
    page_header()
    sidebar_settings()

    meta = st.session_state.survey_meta

    st.subheader("提交方式")

    if meta.get("is_formal", False):
        st.info("当前为正式调研模式。点击提交后会写入 Supabase 云数据库。")
    else:
        st.info("当前为测试 / 预览模式。不会写入正式数据库。")

    st.markdown("您可以只提交问卷，也可以继续完成街景比较。")

    c1, c2 = st.columns(2)

    with c1:
        if st.button("只提交问卷并结束", use_container_width=True):
            try:
                save_basic_if_needed()
            except Exception as e:
                st.error(f"保存问卷失败：{e}")
                return

            go_to("finished_basic_only")

    with c2:
        if st.button("提交问卷并继续街景比较", use_container_width=True):
            try:
                save_basic_if_needed()
            except Exception as e:
                st.error(f"保存问卷失败：{e}")
                return

            with st.spinner("正在生成街景比较题，请稍候..."):
                try:
                    pairs = generate_pairs(
                        respondent_id=st.session_state.respondent_id,
                        n_questions=int(meta.get("n_questions", 60)),
                        reason_ratio=float(meta.get("reason_ratio", 0.2)),
                        is_formal=bool(meta.get("is_formal", False))
                    )
                except Exception as e:
                    st.error(f"生成街景题失败：{e}")
                    return

            st.session_state.pairs = pairs
            st.session_state.current_trial = 0
            st.session_state.trial_start_time = time.time()
            st.session_state.pending_choice = None
            st.session_state.pending_choice_label = None

            go_to("streetview")

    st.markdown("---")

    if st.button("返回上一页"):
        go_to("c")


# =====================================================
# 街景比较页面
# =====================================================

def save_current_choice(choice, choice_label, reasons=None):
    idx = st.session_state.current_trial
    pair = st.session_state.pairs[idx]
    meta = st.session_state.survey_meta

    response_time = time.time() - st.session_state.trial_start_time

    row = {
        "respondent_id": st.session_state.respondent_id,
        "is_test": meta.get("is_test", "测试"),
        "interviewer_id": meta.get("interviewer_id", ""),
        "community": meta.get("community", ""),
        "random_seed": "",
        "n_questions": str(len(st.session_state.pairs)),
        "reason_ratio": str(meta.get("reason_ratio_percent", "")),

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

    if meta.get("is_formal", False):
        save_choice_to_supabase(row)

    st.session_state.current_trial += 1
    st.session_state.pending_choice = None
    st.session_state.pending_choice_label = None
    st.session_state.trial_start_time = time.time()

    if st.session_state.current_trial >= len(st.session_state.pairs):
        go_to("finished_all")
    else:
        rerun()


def streetview_page():
    page_header()
    sidebar_settings()

    st.subheader("街景成对比较")

    total = len(st.session_state.pairs)
    idx = st.session_state.current_trial

    if total == 0:
        st.warning("没有生成街景题。")
        if st.button("结束"):
            go_to("finished_basic_only")
        return

    if idx >= total:
        go_to("finished_all")

    pair = st.session_state.pairs[idx]

    st.progress((idx + 1) / total)
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
        st.markdown("#### 左边")
        st.image(scene_a["image_url"], use_container_width=True)

    with col2:
        st.markdown("#### 右边")
        st.image(scene_b["image_url"], use_container_width=True)

    st.markdown("---")

    if pair["ask_reason"] == 1 and st.session_state.pending_choice is not None:
        st.info(f"您刚才选择了：{st.session_state.pending_choice_label}")

        reasons = st.multiselect(
            "请问主要原因是什么？可多选",
            REASON_OPTIONS
        )

        c1, c2 = st.columns(2)

        with c1:
            if st.button("确认并进入下一题", use_container_width=True):
                try:
                    save_current_choice(
                        st.session_state.pending_choice,
                        st.session_state.pending_choice_label,
                        reasons
                    )
                except Exception as e:
                    st.error(f"保存街景结果失败：{e}")

        with c2:
            if st.button("返回重新选择", use_container_width=True):
                st.session_state.pending_choice = None
                st.session_state.pending_choice_label = None
                rerun()

    else:
        c1, c2, c3 = st.columns(3)

        with c1:
            if st.button("左边更适合", use_container_width=True):
                if pair["ask_reason"] == 1:
                    st.session_state.pending_choice = "A"
                    st.session_state.pending_choice_label = CHOICE_LABELS["A"]
                    rerun()
                else:
                    try:
                        save_current_choice("A", CHOICE_LABELS["A"])
                    except Exception as e:
                        st.error(f"保存街景结果失败：{e}")

        with c2:
            if st.button("右边更适合", use_container_width=True):
                if pair["ask_reason"] == 1:
                    st.session_state.pending_choice = "B"
                    st.session_state.pending_choice_label = CHOICE_LABELS["B"]
                    rerun()
                else:
                    try:
                        save_current_choice("B", CHOICE_LABELS["B"])
                    except Exception as e:
                        st.error(f"保存街景结果失败：{e}")

        with c3:
            if st.button("差不多 / 看不出来", use_container_width=True):
                if pair["ask_reason"] == 1:
                    st.session_state.pending_choice = "Tie"
                    st.session_state.pending_choice_label = CHOICE_LABELS["Tie"]
                    rerun()
                else:
                    try:
                        save_current_choice("Tie", CHOICE_LABELS["Tie"])
                    except Exception as e:
                        st.error(f"保存街景结果失败：{e}")

    st.markdown("---")

    if st.button("结束街景比较", type="secondary"):
        go_to("finished_early")


# =====================================================
# 结束页面
# =====================================================

def finished_page(message):
    page_header()
    sidebar_settings()

    st.success(message)

    meta = st.session_state.survey_meta

    if meta.get("is_formal", False):
        st.info("正式调研模式：已保存到 Supabase 云数据库。")
    else:
        st.info("测试 / 预览模式：本次内容未写入正式数据库。")

    st.markdown("感谢您的参与！")

    if st.button("开始下一份问卷", use_container_width=True):
        reset_all()


# =====================================================
# 主流程
# =====================================================

step = st.session_state.step

if step == "intro":
    intro_page()

elif step == "b1":
    b1_page()

elif step == "b2":
    b2_page()

elif step == "c":
    c_page()

elif step == "submit_choice":
    submit_choice_page()

elif step == "streetview":
    streetview_page()

elif step == "finished_basic_only":
    finished_page("问卷已提交。")

elif step == "finished_all":
    finished_page("问卷与街景比较均已完成。")

elif step == "finished_early":
    finished_page("已提前结束街景比较，已完成的部分已保存。")

else:
    st.session_state.step = "intro"
    rerun()
