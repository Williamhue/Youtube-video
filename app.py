# app.py —— 只读 CSV 的 Streamlit 看板（无外部 API 调用）
import os
from datetime import date

import altair as alt
import pandas as pd
import streamlit as st

st.set_page_config(page_title="YouTube Tracker", layout="wide")

# 可选：页面自动刷新（若未安装则自动跳过）
try:
    from streamlit_autorefresh import st_autorefresh
    st_autorefresh(interval=5 * 60 * 1000, key="auto-refresh")  # 每5分钟刷新一次页面
except Exception:
    pass

# ---- 小样式：让左侧缩略图垂直居中 ----
st.markdown(
    """
<style>
.thumb-cell { display: flex; align-items: center; height: 100%; }
.thumb-cell img { max-width: 100%; }
</style>
""",
    unsafe_allow_html=True,
)

# 每5分钟重新读一次 CSV（线上自动拿到最新数据）
@st.cache_data(ttl=300)
def load_data():
    df = pd.read_csv("data/history.csv")
    df["date"] = pd.to_datetime(df["date"], errors="coerce", utc=True)
    # published_at 可能自带/不带时区，这里统一解析为带 tz 的时间
    df["published_at"] = pd.to_datetime(df["published_at"], errors="coerce", utc=True)
    return df


def days_since(d):
    """返回从发布时间到现在的天数；兼容 tz-naive / tz-aware。"""
    if pd.isna(d):
        return None
    if getattr(d, "tzinfo", None) is None:
        d_utc = d.tz_localize("UTC")
    else:
        d_utc = d.tz_convert("UTC")
    now_utc = pd.Timestamp.now(tz="UTC")
    return (now_utc - d_utc).days


df = load_data()

st.title("📈 YouTube 视频追踪面板")

if df.empty:
    st.info("暂无数据，请先确保仓库中的 data/history.csv 已有内容。")
    st.stop()

# ==== 数据最后更新时间（基于 CSV 内容 + 文件写入时间）====
csv_last_ts = pd.to_datetime(df["date"], errors="coerce").max()
last_file_time_la = None
try:
    mtime = os.path.getmtime("data/history.csv")
    last_file_time_la = (
        pd.to_datetime(mtime, unit="s", utc=True)
        .tz_convert("America/Los_Angeles")
        .strftime("%Y-%m-%d %H:%M:%S %Z")
    )
except Exception:
    pass

msg_left = (
    f"CSV 最新日期：**{csv_last_ts.tz_convert('UTC').date().isoformat()}**"
    if pd.notna(csv_last_ts)
    else "CSV 最新日期：**未知**"
)
msg_right = f"｜ 文件更新时间（LA）：**{last_file_time_la}**" if last_file_time_la else ""
st.info(f"🕒 {msg_left} {msg_right}")

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
        "折线图指标", ["播放量 (Views)", "点赞数 (Likes)", "评论数 (Comments)"], index=0
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
    # 转为日期（去时区）
    min_date = min_d.tz_convert("UTC").date() if pd.notna(min_d) else date.today()
    max_date = max_d.tz_convert("UTC").date() if pd.notna(max_d) else date.today()

    picked = st.date_input("折线图日期范围", [min_date, max_date])
    # 兼容 tuple/list；并做“开始>结束”时自动对调
    if isinstance(picked, (list, tuple)) and len(picked) == 2:
        start_date, end_date = picked
        if start_date > end_date:
            start_date, end_date = end_date, start_date
    else:
        start_date, end_date = (min_date, max_date)

    # 排序依据（含“按发布日期（新→旧）”）
    sort_label = st.selectbox(
        "排序依据", ["按播放量", "按点赞数", "按评论数", "按发布日期（新→旧）"], index=3
    )
    sort_map = {"按播放量": "views", "按点赞数": "likes", "按评论数": "comments"}

    st.write("---")
    # 手动刷新按钮（清缓存并重跑）
    if st.button("🔄 刷新数据（清缓存）", key="refresh"):
        st.cache_data.clear()
        st.rerun()

# 根据频道筛选
filtered_latest = (
    latest if sel_channel == "All" else latest[latest["channel_title"] == sel_channel]
)

# 应用排序
if sort_label == "按发布日期（新→旧）":
    filtered_latest = filtered_latest.sort_values(
        "published_at", ascending=False, na_position="last"
    )
else:
    sort_col = sort_map[sort_label]
    filtered_latest = filtered_latest.sort_values(sort_col, ascending=False)

selected_ids = set(filtered_latest["video_id"].tolist())

# 折线图数据：按日期范围过滤后的历史
show_df = df[df["video_id"].isin(selected_ids)].copy()
start_ts = pd.to_datetime(start_date)  # naive
end_ts = pd.to_datetime(end_date) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)  # naive
# 注意：df["date"] 是带 tz 的，比较时将过滤边界转为 UTC 带 tz
start_ts = start_ts.tz_localize("UTC")
end_ts = end_ts.tz_localize("UTC")

show_df_for_chart = show_df[(show_df["date"] >= start_ts) & (show_df["date"] <= end_ts)].copy()

# 如果选择的结束日期 > 数据最新日期，提示
data_max_day = max_date
if data_max_day and end_date > data_max_day:
    st.warning(
        f"所选结束日期 **{end_date}** 超过当前数据最新日期 **{data_max_day}**，图表只显示到 {data_max_day}。"
    )

st.caption(f"数据按天记录；频道：{sel_channel} ｜ 视频数：{filtered_latest.shape[0]}")

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

# ===== 顶部 KPI 汇总（严格按所选日期范围的“区间增量”） =====
# 先在全量历史上计算“每日增量”，再按所选日期范围切片求和
base_df = df[df["video_id"].isin(selected_ids)].sort_values(["video_id", "date"]).copy()
for col in ["views", "likes", "comments"]:
    inc_col = f"{col}_inc"
    base_df[inc_col] = base_df.groupby("video_id")[col].diff().fillna(0)
    base_df.loc[base_df[inc_col] < 0, inc_col] = 0  # 防抖：出现回退时不计负增量

interval_df = base_df[(base_df["date"] >= start_ts) & (base_df["date"] <= end_ts)].copy()

iv_views = int(interval_df["views_inc"].sum()) if not interval_df.empty else 0
iv_likes = int(interval_df["likes_inc"].sum()) if not interval_df.empty else 0
iv_comments = int(interval_df["comments_inc"].sum()) if not interval_df.empty else 0

i1, i2, i3 = st.columns(3)
i1.metric("本期总增量 · 播放量", f"{iv_views:,}")
i2.metric("本期总增量 · 点赞数", f"{iv_likes:,}")
i3.metric("本期总增量 · 评论数", f"{iv_comments:,}")

# ====== 各视频单卡片 + 折线（带点与数值标签） ======
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
        pub_text = pub.tz_convert("UTC").date().isoformat() if pd.notna(pub) else "未知"
        st.write(f"**发布日期**：{pub_text}  ｜  **已发布**：{dcount} 天")
        c1, c2, c3 = st.columns(3)
        c1.metric("总播放量", f"{int(row['views']):,}")
        c2.metric("总点赞数", f"{int(row['likes']):,}")
        c3.metric("总评论数", f"{int(row['comments']):,}")

        vhist = (
            show_df_for_chart[show_df_for_chart["video_id"] == vid]
            .sort_values("date")
            .copy()
        )
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

        chart_base = alt.Chart(vhist).encode(
            x=alt.X("date:T", title="日期"),
            y=alt.Y("value:Q", title=y_title),
            tooltip=[
                alt.Tooltip("date:T", title="日期"),
                alt.Tooltip("value:Q", title=y_title, format=","),
            ],
        )
        line = chart_base.mark_line()
        points = chart_base.mark_point(size=40)
        labels = chart_base.mark_text(dy=-8).encode(text=alt.Text("value:Q", format=","))

        chart = (line + points + labels).properties(height=220)
        st.altair_chart(chart, use_container_width=True)

# ====== 多视频对比（一张图） ======
st.write("---")
st.subheader("📊 多视频对比（同一张图）")

label_map = dict(
    zip(
        filtered_latest["video_id"],
        filtered_latest["title"] + " — " + filtered_latest["channel_title"],
    )
)
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
    legend_sel = alt.selection_point(fields=["label"], bind="legend", toggle=True)

    base_cmp = (
        alt.Chart(cmp)
        .transform_filter(legend_sel)
        .encode(
            x=alt.X("date:T", title="日期"),
            y=alt.Y("value:Q", title=y_title),
            color=alt.Color("label:N", title="视频"),
            tooltip=[
                alt.Tooltip("label:N", title="视频"),
                alt.Tooltip("date:T", title="日期"),
                alt.Tooltip("value:Q", title=y_title, format=","),
            ],
        )
    ).add_params(legend_sel)

    line_cmp = base_cmp.mark_line()
    points_cmp = base_cmp.mark_point(size=36)
    labels_cmp = base_cmp.mark_text(dy=-8).encode(text=alt.Text("value:Q", format=","))

    compare_chart = (line_cmp + points_cmp + labels_cmp).properties(height=360)
    st.altair_chart(compare_chart, use_container_width=True)

    # === 对比表格（直观汇总，仅保留下载表格 CSV） ===
    meta_cols = ["channel_title", "title", "published_at", "video_url"]
    meta_map = filtered_latest.set_index("video_id")[meta_cols].to_dict(orient="index")

    rows = []
    for vid, g in cmp.groupby("video_id"):
        g = g.sort_values("date")
        first_dt = g["date"].min()
        last_dt = g["date"].max()
        points_cnt = g.shape[0]
        if mode == "每日增量":
            metric_val = g["value"].sum()                # 区间总增量
            peak_val = g["value"].max()                  # 最大单日增量
            metric_label_cn = f"{metric_cn} · 区间总增量"
            peak_label_cn = f"最大单日增量"
        else:
            metric_val = g["value"].iloc[-1]             # 区间末值（累计）
            peak_val = g["value"].max()                  # 累计最大值（一般=末值）
            metric_label_cn = f"{metric_cn} · 区间末值"
            peak_label_cn = f"区间最大值"

        avg_val = g["value"].mean() if points_cnt > 0 else 0

        meta = meta_map.get(vid, {})
        pub = meta.get("published_at")
        pub_text = (
            pd.to_datetime(pub, utc=True).tz_convert("UTC").date().isoformat()
            if pd.notna(pub) else "—"
        )

        rows.append({
            "视频标题": meta.get("title", "—"),
            "频道": meta.get("channel_title", "—"),
            "视频ID": vid,
            "发布日期": pub_text,
            "区间开始": first_dt.tz_convert("UTC").date().isoformat(),
            "区间结束": last_dt.tz_convert("UTC").date().isoformat(),
            metric_label_cn: int(metric_val) if pd.notna(metric_val) else 0,
            "日均值": round(avg_val, 2) if pd.notna(avg_val) else 0,
            peak_label_cn: int(peak_val) if pd.notna(peak_val) else 0,
            "链接": meta.get("video_url", "—"),
        })

    summary_df = pd.DataFrame(rows)

    st.markdown("#### 📋 对比表格（当前指标 & 模式下的区间表现）")
    st.dataframe(
        summary_df[
            ["视频标题", "频道", "视频ID", "发布日期", "区间开始", "区间结束",
             metric_label_cn, "日均值", peak_label_cn, "链接"]
        ],
        use_container_width=True,
        hide_index=True
    )

    # 仅保留：下载表格 CSV
    table_csv = summary_df.to_csv(index=False).encode("utf-8")
    st.download_button(
        label="⬇️ 下载对比表格（CSV）",
        data=table_csv,
        file_name="compare_table.csv",
        mime="text/csv",
    )

st.write("---")
st.caption("数据来源：data/history.csv（由定时任务更新）。时区：America/Los_Angeles。")
