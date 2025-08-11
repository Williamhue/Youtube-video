# app.py —— 只读 CSV 的 Streamlit 看板（无外部 API 调用）
import pandas as pd
import altair as alt
import streamlit as st
from datetime import date
import os
import tempfile

st.set_page_config(page_title="YouTube Tracker", layout="wide")

# 可选：页面自动刷新（若未安装则自动跳过）
try:
    from streamlit_autorefresh import st_autorefresh
    st_autorefresh(interval=5 * 60 * 1000, key="auto-refresh")  # 每5分钟刷新一次页面
except Exception:
    pass

# ---- 小样式：让左侧缩略图垂直居中 ----
st.markdown("""
<style>
.thumb-cell { display: flex; align-items: center; height: 100%; }
.thumb-cell img { max-width: 100%; }
</style>
""", unsafe_allow_html=True)

# 每5分钟重新读一次 CSV（线上自动拿到最新数据）
@st.cache_data(ttl=300)
def load_data():
    df = pd.read_csv("data/history.csv")
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["published_at"] = pd.to_datetime(df["published_at"], errors="coerce")
    return df

def days_since(d):
    """返回从发布时间到现在的天数；兼容 tz-naive / tz-aware。"""
    if pd.isna(d):
        return None
    # 发布时间统一到 UTC
    if getattr(d, "tzinfo", None) is None:
        d_utc = d.tz_localize("UTC")
    else:
        d_utc = d.tz_convert("UTC")
    # 当前 UTC
    now_utc = pd.Timestamp.now(tz="UTC")
    return (now_utc - d_utc).days

df = load_data()

st.title("📈 YouTube 视频追踪面板")

if df.empty:
    st.info("暂无数据，请先确保仓库中的 data/history.csv 已有内容。")
    st.stop()

# 每个视频最新一行（总计信息）
latest = df.sort_values("date").groupby("video_id").tail(1).copy()
# 默认按发布日期倒序（新→旧）
latest = latest.sort_values("published_at", ascending=False, na_position="last")

# -------- 侧边筛选 --------
with st.sidebar:
    st.header("筛选 & 工具")

    # 频道筛选（含 All）
    channels = sorted(latest["channel_title"].dropna().unique().tolist())
    channel_options = ["All"] + channels
    sel_channel = st.selectbox("按频道筛选", channel_options, index=0)

    # 指标与数值模式
    metric_label = st.selectbox(
        "折线图指标",
        ["播放量 (Views)", "点赞数 (Likes)", "评论数 (Comments)"],
        index=0
    )
    metric_map = {
        "播放量 (Views)": ("views", "播放量"),
        "点赞数 (Likes)": ("likes", "点赞数"),
        "评论数 (Comments)": ("comments", "评论数"),
    }
    metric_col, metric_cn = metric_map[metric_label]

    mode = st.radio("数值模式", ["累计", "每日增量"], index=0, horizontal=True)

    # 日期范围（影响：折线图、顶部区间增量KPI）
    min_d = df["date"].min()
    max_d = df["date"].max()
    min_date = min_d.date() if pd.notna(min_d) else date.today()
    max_date = max_d.date() if pd.notna(max_d) else date.today()
    picked = st.date_input("折线图日期范围", [min_date, max_date])
    if isinstance(picked, list) and len(picked) == 2:
        start_date, end_date = picked
    else:
        start_date, end_date = (min_date, max_date)

    # 排序依据（含“按发布日期（新→旧）”）
    sort_label = st.selectbox(
        "排序依据",
        ["按播放量", "按点赞数", "按评论数", "按发布日期（新→旧）"],
        index=3
    )
    sort_map = {"按播放量": "views", "按点赞数": "likes", "按评论数": "comments"}

    st.write("---")
    # 手动刷新按钮（清缓存并重跑）
    if st.button("🔄 刷新数据（清缓存）"):
        st.cache_data.clear()
        st.experimental_rerun()

# 根据频道筛选
filtered_latest = latest if sel_channel == "All" else latest[latest["channel_title"] == sel_channel]

# 应用排序
if sort_label == "按发布日期（新→旧）":
    filtered_latest = filtered_latest.sort_values("published_at", ascending=False, na_position="last")
else:
    sort_col = sort_map[sort_label]
    filtered_latest = filtered_latest.sort_values(sort_col, ascending=False)

selected_ids = set(filtered_latest["video_id"].tolist())

# 折线图数据：按日期范围过滤后的历史
show_df = df[df["video_id"].isin(selected_ids)].copy()
start_ts = pd.to_datetime(start_date)  # naive
end_ts = pd.to_datetime(end_date) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)  # naive
show_df_for_chart = show_df[(show_df["date"] >= start_ts) & (show_df["date"] <= end_ts)].copy()

st.caption(
    f"数据按天记录；折线图支持切换与。频道：{sel_channel} ｜ 视频数：{filtered_latest.shape[0]}"
)

# 全局 KPI（总量/率）：针对当前频道筛选（各视频“最新一行”加总）
kpi_scope = filtered_latest.copy()
total_views = int(kpi_scope["views"].sum())
total_likes = int(kpi_scope["likes"].sum())
total_comments = int(kpi_scope["comments"].sum())
like_rate = (total_likes / total_views * 100) if total_views > 0 else 0.0
comment_rate = (total_comments / total_views * 100) if total_views > 0 else 0.0

k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("总播放量（截至最新）", f"{total_views:,}")
k2.metric("总点赞数（截至最新）", f"{total_likes:,}")
k3.metric("总评论数（截至最新）", f"{total_comments:,}")
k4.metric("Like Rate（点赞率）", f"{like_rate:.2f}%")
k5.metric("Comment Rate（评论率）", f"{comment_rate:.2f}%")

# 顶部 KPI 汇总（按当前日期筛选后的“区间增量”，全体视频）
interval_df = show_df_for_chart.sort_values(["video_id", "date"]).copy()
for col in ["views", "likes", "comments"]:
    inc_col = f"{col}_inc"
    interval_df[inc_col] = interval_df.groupby("video_id")[col].diff().fillna(0)
    interval_df.loc[interval_df[inc_col] < 0, inc_col] = 0

iv_views = int(interval_df["views_inc"].sum()) if not interval_df.empty else 0
iv_likes = int(interval_df["likes_inc"].sum()) if not interval_df.empty else 0
iv_comments = int(interval_df["comments_inc"].sum()) if not interval_df.empty else 0

i1, i2, i3 = st.columns(3)
i1.metric("本期总增量 · 播放量", f"{iv_views:,}")
i2.metric("本期总增量 · 点赞数", f"{iv_likes:,}")
i3.metric("本期总增量 · 评论数", f"{iv_comments:,}")

# ====== 各视频单卡片 + 折线 ======
for _, row in filtered_latest.iterrows():
    vid = row["video_id"]
    col1, col2 = st.columns([1, 3])

    with col1:
        thumb = row.get("thumbnail_url", None)
        st.markdown("<div class='thumb-cell'>", unsafe_allow_html=True)
        if pd.notna(thumb) and thumb:
            st.image(thumb, use_container_width=True)
        st.markdown("</div>", unsafe_allow_html=True)
        st.markdown(f"[▶️ 打开视频]({row['video_url']})")

    with col2:
        st.subheader(f"{row['title']}")
        st.write(f"**频道**：{row['channel_title']}")
        pub = row["published_at"]
        dcount = days_since(pub)
        pub_text = pub.date().isoformat() if pd.notna(pub) else "未知"
        st.write(f"**发布日期**：{pub_text}  ｜  **已发布**：{dcount} 天")
        c1, c2, c3 = st.columns(3)
        c1.metric("总播放量", f"{int(row['views']):,}")
        c2.metric("总点赞数", f"{int(row['likes']):,}")
        c3.metric("总评论数", f"{int(row['comments']):,}")

        vhist = show_df_for_chart[show_df_for_chart["video_id"] == vid].sort_values("date").copy()
        if vhist.empty:
            st.info("当前日期范围内无数据")
            continue

        if mode == "每日增量":
            vhist["value"] = vhist[metric_col].diff().fillna(0)
            vhist.loc[vhist["value"] < 0, "value"] = 0
            y_title = f"{metric_cn}（每日增量）"
        else:
            vhist["value"] = vhist[metric_col]
            y_title = f"{metric_cn}（累计）"

base = (
    alt.Chart(vhist)
       .encode(
           x=alt.X("date:T", title="日期"),
           y=alt.Y("value:Q", title=y_title),
           tooltip=[
               alt.Tooltip("date:T", title="日期"),
               alt.Tooltip("value:Q", title=y_title, format=",")
           ]
       )
)

line = base.mark_line()
points = base.mark_point(size=40)
labels = base.mark_text(dy=-8).encode(text=alt.Text("value:Q", format=","))

chart = (line + points + labels).properties(height=220)
st.altair_chart(chart, use_container_width=True)


# ====== 多视频对比（一张图） + 下载按钮（支持图例点击显隐）=====
st.write("---")
st.subheader("📊 多视频对比（同一张图）")

label_map = dict(zip(
    filtered_latest["video_id"],
    filtered_latest["title"] + " — " + filtered_latest["channel_title"]
))
default_compare_ids = list(label_map.keys())
compare_labels = st.multiselect(
    "选择参与对比的视频",
    options=[label_map[v] for v in default_compare_ids],
    default=[label_map[v] for v in default_compare_ids],
)
compare_ids = {vid for vid, label in label_map.items() if label in compare_labels}

cmp = show_df_for_chart[show_df_for_chart["video_id"].isin(compare_ids)].copy()
if cmp.empty:
    st.info("当前筛选下没有可对比的数据")
else:
    cmp = cmp.sort_values(["video_id", "date"]).copy()
    if mode == "每日增量":
        cmp["value"] = cmp.groupby("video_id")[metric_col].diff().fillna(0)
        cmp.loc[cmp["value"] < 0, "value"] = 0
        y_title = f"{metric_cn}（每日增量）"
    else:
        cmp["value"] = cmp[metric_col]
        y_title = f"{metric_cn}（累计）"

    cmp["label"] = cmp["video_id"].map(label_map)

    # 图例点击显隐交互：legend 绑定的 selection
    legend_sel = alt.selection_point(fields=["label"], bind="legend", toggle=True)

    compare_chart = (
        alt.Chart(cmp)
           .transform_filter(legend_sel)
           .mark_line()
           .encode(
               x=alt.X("date:T", title="日期"),
               y=alt.Y("value:Q", title=y_title),
               color=alt.Color("label:N", title="视频"),
               tooltip=[
                   alt.Tooltip("label:N", title="视频"),
                   alt.Tooltip("date:T", title="日期"),
                   alt.Tooltip("value:Q", title=y_title),
               ]
           )
           .add_params(legend_sel)
           .properties(height=360)
    )
    st.altair_chart(compare_chart, use_container_width=True)

    # 下载 CSV
    csv_bytes = cmp[["date", "label", "video_id", "value"]].to_csv(index=False).encode("utf-8")
    st.download_button(
        label="⬇️ 下载对比数据（CSV）",
        data=csv_bytes,
        file_name="compare_data.csv",
        mime="text/csv"
    )

    # 下载 PNG（需要 vl-convert-python；未安装会提示）
    png_ready = True
    png_bytes = None
    try:
        with tempfile.TemporaryDirectory() as td:
            out_path = os.path.join(td, "compare.png")
            compare_chart.save(out_path, format="png", scale_factor=2)
            with open(out_path, "rb") as f:
                png_bytes = f.read()
    except Exception:
        png_ready = False
        st.info("如需导出 PNG，请在环境中安装：`pip install vl-convert-python`，装好后刷新页面。")

    if png_ready and png_bytes:
        st.download_button(
            label="🖼️ 下载对比图（PNG）",
            data=png_bytes,
            file_name="compare_chart.png",
            mime="image/png"
        )

st.write("---")
st.caption("数据来源：仓库内 data/history.csv（由定时任务更新）。时区：America/Los_Angeles。")
