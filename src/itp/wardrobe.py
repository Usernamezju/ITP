"""Deterministic outfit recommendations and bounding-box body estimates for ITP.

Everything this feature needs lives in this file: the catalogue of outfits is a
plain Python structure, the body descriptors are computed from a local GLB and
there is no network access, no randomness and no external data file.  The same
request therefore always produces the same answer.

Two independent parts:

* :func:`analyze_glb` reads vertex positions out of a GLB container and turns
  them into a handful of body descriptors.  Every number comes from the model's
  bounding box and from 20 horizontal silhouette slices, so these are estimates
  for garment cuts and **not** anthropometric or medical measurements.  They
  describe the mesh, not the person.
* :func:`recommend_outfits` scores the catalogue against those descriptors and
  the optional style/season/occasion filters, then sorts by score and ``id``.

GLB reading rules
-----------------
* The file is read as a binary glTF container: ``glTF`` magic, version 2, one
  JSON chunk and one BIN chunk.  Only embedded buffers are used; a buffer that
  declares a ``uri`` (external file or data URL) is unsupported.
* Only ``meshes[*].primitives[*].attributes.POSITION`` accessors are decoded,
  with ``componentType`` 5126 (float32) and ``type`` VEC3.  Anything else -
  Draco compression, sparse accessors, a different component type, an accessor
  that leaves the BIN chunk - degrades to ``None``.
* A file above 512 MiB, more than 5,000,000 declared vertices, a truncated
  container or an unreadable file also return ``None``.  Callers fall back to
  the generic catalogue instead of failing.
* Y is the up axis, as the glTF specification requires, so the Y span is the
  stature, the X span the width and the Z span the depth.

Silhouette bands
----------------
The stature is split into 20 equal bands counted **downwards from the top of
the model**: band 0 is the topmost 5%, band 19 covers the feet.  A band's value
is ``(max X - min X) / stature`` over the vertices inside it, so every value is
a fraction of the stature in the 0-1 range.

Descriptors
-----------
``shoulder``   widest band in 15%-35% from the top (the requested 15%-32%
               window rounded out to whole 5% bands).  Bands wider than 1.8x
               the median of bands 6-13 are treated as outstretched arms and
               left out of the estimate, so a T-pose model does not read as
               "broad" merely because its arms are horizontal.
``waist``      narrowest band in 30%-55%; reported as waist / shoulder.
``hip``        widest band in 50%-70%; the centre of that band is the hip line.
``leg ratio``  ``1 - hip line``, the share of the stature below the hip line.
``thickness``  Z span / stature.  ``depth ratio`` Z span / X span is reported
               next to it, but it is not used for the volume family: for a
               narrow body a large depth/width says more about the narrow width
               than about bulk, while depth/stature is pose independent.

Thresholds (chosen for garment cuts, matched to the band geometry above):
``shoulder`` < 0.22 slim, < 0.27 regular, else broad.
``thickness`` < 0.17 light, < 0.24 medium, else heavy.
``leg ratio`` < 0.375 short-leg, < 0.425 balanced-leg, else long-leg.  The hip
window caps this descriptor at roughly 0.33-0.48, so the thresholds split that
reachable range evenly instead of splitting 0-1.

Scoring
-------
``SCORE_BASE`` (0.30) keeps every outfit recommendable.  A tag hit adds
``TAG_WEIGHTS`` - build 0.22 > volume 0.16 > legs 0.10, so the skeleton and
volume families outweigh the proportion family.  A filter hit adds
``FILTER_WEIGHTS``; a filter that is set but not matched subtracts
``FILTER_PENALTIES``, which re-ranks the catalogue without ever emptying it.
The best possible score is 1.00, the worst 0.10, and ties keep catalogue order,
so a generic request stays an editorial list instead of an alphabetical one.
"""

import json
import math
import struct
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np

from itp.storage import Store

METHOD = "从三维模型包围盒与轮廓切片估算"
PROFILE_BANDS = 20
DEFAULT_LIMIT = 6
MIN_LIMIT = 1
MAX_LIMIT = 24

GLB_MAGIC = b"glTF"
GLB_VERSION = 2
JSON_CHUNK = 0x4E4F534A
BIN_CHUNK = 0x004E4942
FLOAT_COMPONENT = 5126
VEC3_BYTES = 12
DRACO_EXTENSION = "KHR_draco_mesh_compression"
MAX_GLB_BYTES = 512 * 1024 * 1024
MAX_VERTICES = 5_000_000
MIN_VERTICES = 3

SHOULDER_BANDS = (3, 7)
WAIST_BANDS = (6, 11)
HIP_BANDS = (10, 14)
TORSO_BANDS = (6, 14)
ARM_SPIKE_RATIO = 1.8

BUILD_THRESHOLDS = (0.22, 0.27)
THICKNESS_THRESHOLDS = (0.17, 0.24)
LEG_THRESHOLDS = (0.375, 0.425)

TAG_FAMILIES: dict[str, tuple[str, ...]] = {
    "build": ("slim", "regular", "broad"),
    "volume": ("light", "medium", "heavy"),
    "legs": ("short-leg", "balanced-leg", "long-leg"),
}
BUILD_LABELS = {"slim": "修长", "regular": "匀称", "broad": "宽阔"}
VOLUME_LABELS = {"light": "轻量", "medium": "适中", "heavy": "厚实"}
LEG_LABELS = {"short-leg": "短腿型", "balanced-leg": "标准比例", "long-leg": "长腿型"}
POSE_LABELS = {
    "original": "原始姿态",
    "a-pose": "A-Pose",
    "t-pose": "T-Pose",
    "custom": "自定义姿势",
}
POSE_UNRECORDED = "未记录"

SCORE_BASE = 0.30
TAG_WEIGHTS = {"build": 0.22, "volume": 0.16, "legs": 0.10}
FILTER_WEIGHTS = {"style": 0.10, "season": 0.06, "occasion": 0.06}
FILTER_PENALTIES = {"style": 0.10, "season": 0.05, "occasion": 0.05}

BUILD_HINTS = {
    "slim": "肩宽较窄，适合收肩或落肩结构",
    "regular": "肩线接近标准比例，正肩与插肩都能驾驭",
    "broad": "肩背较宽，避开厚垫肩，选插肩或落肩柔化线条",
}
VOLUME_HINTS = {
    "light": "体量轻，贴身与垂坠面料都撑得住",
    "medium": "体量适中，结构感与柔软面料都好用",
    "heavy": "体量厚实，优先竖向线条与有支撑的面料",
}
LEG_HINTS = {
    "short-leg": "腿身比偏短，优先高腰与短上装",
    "balanced-leg": "腿身比标准，常规腰线即可",
    "long-leg": "腿身比偏长，低腰与长外套都压得住",
}

CATALOG: tuple[dict[str, Any], ...] = (
    {
        "id": "soft-tailoring",
        "image_query": "通勤 针织开衫 西裤 职场穿搭",
        "name": "柔雾通勤",
        "tagline": "把锋利收进软结构里。",
        "story": "以雾面针织与垂坠西裤搭建的通勤骨架：肩部不加强调，用落肩线让窄肩显得从容；"
                 "上衣塞进高腰裤，把偏长的腿身比还给自己。全身只留三种低饱和色，远看是一片安静"
                 "的灰绿，近看是细密的罗纹与斜纹。",
        "style": "通勤",
        "season": "四季",
        "occasion": "通勤办公",
        "palette": ["#c9d6bd", "#4a5b52", "#b9a892"],
        "items": [
            {"category": "外套", "name": "落肩薄呢外套", "color": "#4a5b52",
             "note": "肩线自然下落，弱化肩部体量"},
            {"category": "上装", "name": "细罗纹半高领针织", "color": "#c9d6bd",
             "note": "贴身不紧绷，前襟塞进裤腰"},
            {"category": "下装", "name": "高腰直筒西裤", "color": "#b9a892",
             "note": "高腰线延长腿部，裤脚刚好盖住脚背"},
            {"category": "鞋履", "name": "方头乐福鞋", "color": "#4a5b52",
             "note": "低跟方头，稳定整体比例"},
            {"category": "配饰", "name": "细框耳环", "color": "#b9a892",
             "note": "小体量金属，不抢肩颈线条"},
        ],
        "tags": ("slim", "light", "long-leg"),
        "reasons": {
            "slim": "落肩结构把肩线藏进面料里，窄肩不会被硬挺的正肩衬得更单薄。",
            "light": "薄呢与细针织贴而不勒，轻体量不必靠垫肩撑起轮廓。",
            "long-leg": "高腰直筒裤把偏长的腿身比收进一条干净的垂直线上。",
        },
        "tips": ["外套长度停在胯骨上方两指，避免压短腿线",
                 "鞋与裤同色，下半身连成一条线"],
        "avoid": "肩部带硬质垫肩或横向拼接的上装，会把窄肩轮廓顶得更单薄。",
    },
    {
        "id": "city-knit",
        "image_query": "秋季 粗针开衫 锥形西裤 通勤",
        "name": "城市针织",
        "tagline": "针织的松弛，配西裤的规矩。",
        "story": "把秋天的城市穿在身上：驼色粗针开衫配灰色锥形西裤，比例匀称的身形只需要一条"
                 "清楚的中线。开衫不必扣满，留出内搭的白色圆领，让层次从领口开始；裤脚略收，"
                 "露出一截脚踝，通勤也能走得轻快。",
        "style": "通勤",
        "season": "秋",
        "occasion": "通勤办公",
        "palette": ["#c8b49a", "#6f7276", "#f2efe9"],
        "items": [
            {"category": "上装", "name": "粗针开衫", "color": "#c8b49a",
             "note": "只扣中间一颗，腰线自然出现"},
            {"category": "内搭", "name": "纯棉圆领 T 恤", "color": "#f2efe9",
             "note": "白色领口提供干净的层次起点"},
            {"category": "下装", "name": "灰色锥形西裤", "color": "#6f7276",
             "note": "裤脚微收，露出脚踝一截"},
            {"category": "鞋履", "name": "白色薄底球鞋", "color": "#f2efe9",
             "note": "与内搭同色，重心回到上半身"},
            {"category": "包袋", "name": "深灰托特包", "color": "#6f7276",
             "note": "硬挺廓形，替整体压住中线"},
        ],
        "tags": ("regular", "medium", "balanced-leg"),
        "reasons": {
            "regular": "开衫肩线取正肩与落肩之间，匀称比例不必刻意矫正。",
            "medium": "中针距羊毛有骨架也有垂感，体量适中穿起来不塌不鼓。",
            "balanced-leg": "九分锥形裤落在标准腰线上，裤脚收口让小腿自然结束。",
        },
        "tips": ["内搭与鞋同色，视觉重心回到上半身",
                 "西裤选中腰而不是低腰，比例最稳"],
        "avoid": "全身同色同材质，会让匀称的比例失去层次、显得平板。",
    },
    {
        "id": "sharp-shoulder",
        "image_query": "冬季 羊绒大衣 高领 商务通勤",
        "name": "直角廓形",
        "tagline": "宽肩不是负担，是结构。",
        "story": "既然肩背本来就有分量，就不必再与它对抗：一件垂顺的深色大衣顺着肩线落下，用直身"
                 "剪裁把宽度收进干净的直线里。内搭高领与同色锥形裤保持纵向连续，会议桌上的轮廓"
                 "利落、不臃肿，走动时衣摆还能带出一点空气感。",
        "style": "通勤",
        "season": "冬",
        "occasion": "商务会议",
        "palette": ["#2f3a3f", "#8f9aa1", "#d9d2c5"],
        "items": [
            {"category": "外套", "name": "直身羊绒大衣", "color": "#2f3a3f",
             "note": "顺肩直落，不做横向加宽"},
            {"category": "内搭", "name": "细针高领衫", "color": "#8f9aa1",
             "note": "高领把视线提到面部，弱化肩宽"},
            {"category": "下装", "name": "深色直筒西裤", "color": "#2f3a3f",
             "note": "与大衣同色，纵向连成一片"},
            {"category": "鞋履", "name": "尖头短靴", "color": "#2f3a3f",
             "note": "鞋头延伸腿线，收住下摆"},
            {"category": "配饰", "name": "细长围巾", "color": "#d9d2c5",
             "note": "垂挂在胸前形成第二条竖线"},
        ],
        "tags": ("broad", "heavy", "balanced-leg"),
        "reasons": {
            "broad": "直身大衣的肩线顺延肩点，宽度变成气场而不是体积。",
            "heavy": "羊绒混纺垂坠不贴身，厚实体量被一条纵向直线收住。",
            "balanced-leg": "标准腿身比配同色直筒裤，裤脚与鞋面几乎连成一线。",
        },
        "tips": ["选插肩或顺肩剪裁，避开方形硬垫肩",
                 "围巾不要绕成团，垂下来才有纵向线条"],
        "avoid": "双排扣、腰部横断拼色或短款羽绒，会在肩背最宽处再叠一层横向。",
    },
    {
        "id": "weekend-linen",
        "image_query": "夏季 亚麻衬衫 阔腿裤 度假",
        "name": "周末亚麻",
        "tagline": "亚麻的褶，是夏天的刻度。",
        "story": "松弛感来自面料的呼吸：米白亚麻衬衫敞着穿，里面是一件洗旧的条纹背心，下身是"
                 "卷起裤脚的浅卡其阔腿裤。匀称的身形不需要多余修饰，让亚麻自然的褶皱留在手臂"
                 "和衣摆上，配一双草编鞋，像刚从海边回来。",
        "style": "休闲",
        "season": "夏",
        "occasion": "周末出行",
        "palette": ["#efe7d7", "#b9c6cf", "#cdbb9c"],
        "items": [
            {"category": "上装", "name": "米白亚麻衬衫", "color": "#efe7d7",
             "note": "敞着穿，只塞前襟一半"},
            {"category": "内搭", "name": "条纹棉背心", "color": "#b9c6cf",
             "note": "细条纹提供唯一的图案"},
            {"category": "下装", "name": "卡其阔腿裤", "color": "#cdbb9c",
             "note": "裤脚卷两折，露出脚踝"},
            {"category": "鞋履", "name": "草编平底鞋", "color": "#cdbb9c",
             "note": "与裤子同色系，延长腿线"},
            {"category": "配饰", "name": "编织腰带", "color": "#8a7358",
             "note": "细腰带在腰线处做一次收束"},
        ],
        "tags": ("regular", "light", "long-leg"),
        "reasons": {
            "regular": "宽松敞穿的版型不挑肩线，匀称比例穿出自然的松弛。",
            "light": "亚麻重量很轻，轻体量恰好让面料自己形成褶皱线条。",
            "long-leg": "阔腿裤卷起两折露出脚踝，偏长的腿身比更显清爽。",
        },
        "tips": ["同色系叠穿，让褶皱成为唯一的纹理",
                 "下摆只塞前半，腰线出现又不紧绷"],
        "avoid": "挺括的涂层面料或收腰紧身裙，会把亚麻的松弛感全部磨掉。",
    },
    {
        "id": "denim-ease",
        "image_query": "牛仔外套 高腰直筒牛仔裤 休闲",
        "name": "丹宁松弛",
        "tagline": "一条好牛仔裤，能兜住所有比例。",
        "story": "短腿身比最怕被裤型拖住：选高腰直筒丹宁，把腰线提到肋骨下缘，上身用短款白衬衫"
                 "把视觉重心留在上半身。外面罩一件水洗丹宁夹克，同色不同深浅的层次让整个人看"
                 "起来轻快随意，又不会显矮。",
        "style": "休闲",
        "season": "四季",
        "occasion": "日常休闲",
        "palette": ["#4a6b8a", "#dfe4e8", "#8a99a8"],
        "items": [
            {"category": "外套", "name": "水洗丹宁夹克", "color": "#6b86a0",
             "note": "下摆停在腰线以上"},
            {"category": "上装", "name": "短款白衬衫", "color": "#dfe4e8",
             "note": "短款不压腰线，塞进裤腰更利落"},
            {"category": "下装", "name": "高腰直筒牛仔裤", "color": "#4a6b8a",
             "note": "高腰把腿线抬起一截"},
            {"category": "鞋履", "name": "厚底帆布鞋", "color": "#dfe4e8",
             "note": "与衬衫同色，脚踝处不断线"},
            {"category": "配饰", "name": "细皮带", "color": "#8a99a8",
             "note": "明确标出被抬高的腰线"},
        ],
        "tags": ("slim", "medium", "short-leg"),
        "reasons": {
            "slim": "短款夹克的肩线收在肩点以内，窄肩反而显得利落。",
            "medium": "中厚丹宁有支撑力，不需要靠垫肩或填充制造体量。",
            "short-leg": "高腰直筒把腰线抬起一截，腿身比被这条线重新分配。",
        },
        "tips": ["外套下摆停在腰线以上，别盖住臀线",
                 "鞋与裤子同色，视觉上把腿延长"],
        "avoid": "低腰或过度堆叠的宽松裤型，会把本就不长的腿线再压一段。",
    },
    {
        "id": "field-jacket",
        "image_query": "工装夹克 工装裤 中帮靴 户外",
        "name": "工装旅人",
        "tagline": "把宽阔的肩膀，交给工装。",
        "story": "宽肩厚背在工装上反而好办：一件抽绳工装夹克把腰线自己系出来，硬挺的斜纹布顺着"
                 "肩点落下，不额外加宽。下身配锥形工装裤与中帮靴，裤脚收进靴口，短腿身比被靴筒"
                 "那一截竖向线条接住，郊外走路也稳。",
        "style": "休闲",
        "season": "秋",
        "occasion": "户外郊游",
        "palette": ["#6b6a52", "#3f4636", "#c2b79b"],
        "items": [
            {"category": "外套", "name": "抽绳工装夹克", "color": "#6b6a52",
             "note": "抽绳系在自然腰线略高处"},
            {"category": "内搭", "name": "华夫格长袖衫", "color": "#c2b79b",
             "note": "细腻纹理，不增加体量"},
            {"category": "下装", "name": "锥形工装裤", "color": "#3f4636",
             "note": "裤脚收进靴口，形成竖向一段"},
            {"category": "鞋履", "name": "中帮皮靴", "color": "#3f4636",
             "note": "与裤子同色，替腿补上高度"},
            {"category": "配饰", "name": "帆布工具包", "color": "#c2b79b",
             "note": "斜挎在胯侧，压住下摆"},
        ],
        "tags": ("broad", "medium", "short-leg"),
        "reasons": {
            "broad": "工装肩部只做顺肩，不收不扩，宽肩成为版型的一部分。",
            "medium": "斜纹棉有硬度也有透气性，中等体量穿起来挺而不鼓。",
            "short-leg": "裤脚塞进中帮靴，竖向的一截靴筒补上了腿长。",
        },
        "tips": ["抽绳系在自然腰线略高处，比例立刻变化",
                 "深色靴裤组合，让下半身连成一体"],
        "avoid": "过短的夹克配低腰裤，会在腰臀之间留出一道横向断层。",
    },
    {
        "id": "street-layer",
        "image_query": "冬日 长款大衣 连帽卫衣 街头",
        "name": "分层街头",
        "tagline": "夜色里，轮廓比颜色更响。",
        "story": "宽阔厚实的身体在冬天的街头是优势：长款大衣压住整体，里面叠一件短款羽绒马甲，"
                 "用长短差把层次拉开。下身是束脚运动裤与厚底跑鞋，偏长的腿身比撑得起宽松的体积，"
                 "走在夜里像一段移动的黑色剪影。",
        "style": "街头",
        "season": "冬",
        "occasion": "城市夜出",
        "palette": ["#22242a", "#5b6068", "#9aa0a6"],
        "items": [
            {"category": "外套", "name": "长款羊毛大衣", "color": "#22242a",
             "note": "过膝长度，肩部顺肩直落"},
            {"category": "内搭", "name": "短款羽绒马甲", "color": "#5b6068",
             "note": "停在腰线，与大衣形成长短差"},
            {"category": "上装", "name": "灰色连帽卫衣", "color": "#9aa0a6",
             "note": "帽子垂在背后，不立起来"},
            {"category": "下装", "name": "束脚运动裤", "color": "#22242a",
             "note": "束脚收在脚踝，腿线不断"},
            {"category": "鞋履", "name": "厚底跑鞋", "color": "#9aa0a6",
             "note": "厚底撑住宽松的上装体积"},
        ],
        "tags": ("broad", "heavy", "long-leg"),
        "reasons": {
            "broad": "长大衣肩部顺肩直落，宽阔的肩背被包进统一的垂直线。",
            "heavy": "层次靠长短差而不是厚度，厚实体量不会变成臃肿。",
            "long-leg": "束脚裤与厚底鞋之间留白很少，偏长的腿线撑得住宽松上装。",
        },
        "tips": ["外套长度过膝，内层短款停在腰线",
                 "全身三色以内，用材质区分层次"],
        "avoid": "短款羽绒配宽松工装裤，会把厚实的体量堆在腰腹最宽处。",
    },
    {
        "id": "skate-shadow",
        "image_query": "落肩卫衣 工装裤 滑板鞋 街头",
        "name": "滑板宽影",
        "tagline": "宽肩配宽裤，稳。",
        "story": "把宽阔的肩线交给落肩卫衣，帽子自然垂在背后，横向的宽度就有了归属。下身用直筒"
                 "工装裤与低帮滑板鞋，裤脚盖住鞋面一半，偏长的腿身比让整条下半身连成一段干净的"
                 "竖线，站在滑板旁边也不显得笨重。",
        "style": "街头",
        "season": "春",
        "occasion": "街头社交",
        "palette": ["#3b4a5a", "#d5d9dd", "#7d8b96"],
        "items": [
            {"category": "上装", "name": "落肩连帽卫衣", "color": "#3b4a5a",
             "note": "肩点藏进袖窿，宽度被版型消化"},
            {"category": "内搭", "name": "白色长袖 T 恤", "color": "#d5d9dd",
             "note": "下摆露出一指，制造层次"},
            {"category": "下装", "name": "直筒工装裤", "color": "#7d8b96",
             "note": "裤脚盖住鞋面一半，腿线不截断"},
            {"category": "鞋履", "name": "低帮滑板鞋", "color": "#d5d9dd",
             "note": "扁薄鞋型，街头感的起点"},
            {"category": "配饰", "name": "棒球帽", "color": "#3b4a5a",
             "note": "帽檐压低，视线集中在上半身"},
        ],
        "tags": ("broad", "medium", "long-leg"),
        "reasons": {
            "broad": "落肩卫衣把肩点藏进袖窿，宽度被版型自然消化。",
            "medium": "中等克重卫衣有垂坠也有形，不靠厚度撑轮廓。",
            "long-leg": "裤脚盖住鞋面一半，偏长的腿线不被截断。",
        },
        "tips": ["帽子垂在背后，比立起来更显肩窄",
                 "裤脚略堆叠，街头感来自这一点余量"],
        "avoid": "紧身卫衣或收口运动裤，会把宽肩与长腿的比例反衬得突兀。",
    },
    {
        "id": "mono-drop",
        "image_query": "全黑穿搭 长版T恤 短裤 厚底靴",
        "name": "单色坠落",
        "tagline": "一身黑，也是一种结构。",
        "story": "窄肩轻体量最适合被一条直线概括：黑色落肩长 T 下摆盖过胯部，配同色直筒短裤与"
                 "厚底靴，整条轮廓从肩到脚不断线。短腿身比靠厚底与裤长一起修正，站在人群里是"
                 "安静的一块黑。",
        "style": "街头",
        "season": "夏",
        "occasion": "音乐现场",
        "palette": ["#1c1c1e", "#3a3a3d", "#8d8d92"],
        "items": [
            {"category": "上装", "name": "落肩长版黑 T", "color": "#1c1c1e",
             "note": "下摆盖过胯线，肩线放低"},
            {"category": "下装", "name": "直筒黑短裤", "color": "#3a3a3d",
             "note": "长度停在膝上，腿线不被吃掉"},
            {"category": "鞋履", "name": "厚底切尔西靴", "color": "#1c1c1e",
             "note": "厚底抬起视觉重心"},
            {"category": "配饰", "name": "细链条项链", "color": "#8d8d92",
             "note": "金属集中在上半身，引导视线"},
            {"category": "包袋", "name": "斜挎小包", "color": "#3a3a3d",
             "note": "包带斜拉出一条对角线"},
        ],
        "tags": ("slim", "light", "short-leg"),
        "reasons": {
            "slim": "落肩长版 T 把肩线放低，窄肩被拉进一条松弛的横向。",
            "light": "薄棉单穿就够，轻体量不需要叠穿制造厚度。",
            "short-leg": "短裤停在膝上、厚底靴抬起重心，腿线被重新分配。",
        },
        "tips": ["上衣下摆盖过胯线，比例交给裤长处理",
                 "金属配饰集中在上半身，视线跟着上移"],
        "avoid": "横向拼接或宽腰带，会在窄肩上再划一道横线。",
    },
    {
        "id": "track-flow",
        "image_query": "运动风衣 速干长袖 跑步长裤",
        "name": "疾速线条",
        "tagline": "风是唯一的装饰。",
        "story": "训练装也可以有比例：修身但不紧的速干长袖配锥形运动长裤，侧面一条同色饰线把"
                 "小腿延伸到脚踝。匀称的身形穿运动服最容易保持利落，外套一件轻薄风衣，跑起来时"
                 "衣摆向后，偏长的腿身比自然显露。",
        "style": "运动",
        "season": "四季",
        "occasion": "日常训练",
        "palette": ["#1f3a4d", "#7fb2c9", "#e8eef1"],
        "items": [
            {"category": "上装", "name": "速干长袖", "color": "#1f3a4d",
             "note": "插肩袖顺着肩型走"},
            {"category": "外套", "name": "轻薄风衣", "color": "#7fb2c9",
             "note": "下摆略短于上衣，跑动时有速度感"},
            {"category": "下装", "name": "锥形运动长裤", "color": "#1f3a4d",
             "note": "侧面饰线一路延伸到脚踝"},
            {"category": "鞋履", "name": "缓震跑鞋", "color": "#e8eef1",
             "note": "浅色鞋面收住深色裤脚"},
            {"category": "配饰", "name": "运动手表", "color": "#e8eef1",
             "note": "腕上一点浅色，打破全深配色"},
        ],
        "tags": ("regular", "medium", "long-leg"),
        "reasons": {
            "regular": "插肩袖顺着肩型走，匀称比例不必刻意修饰。",
            "medium": "速干面料贴身而不勒，中等体量活动时最舒服。",
            "long-leg": "锥形裤侧面饰线一路向下，偏长的腿线被强调出来。",
        },
        "tips": ["风衣下摆略短于上衣，跑动时更有速度感",
                 "深浅同色系搭配，避免运动服的杂乱感"],
        "avoid": "过于宽大的卫裤配长外套，会在脚踝处堆出多余的体积。",
    },
    {
        "id": "trail-shell",
        "image_query": "冲锋衣 软壳裤 登山鞋 户外",
        "name": "山径外壳",
        "tagline": "硬壳之下，比例自己说话。",
        "story": "宽肩厚背穿硬壳反而好看：冲锋衣的肩部顺肩剪裁，把宽度变成防护面。下身是收脚"
                 "软壳裤与中帮登山鞋，裤脚扎进鞋帮，短腿身比靠这一截竖向的鞋筒找回来；腰带收紧"
                 "一点，行进中的轮廓干净、有方向。",
        "style": "运动",
        "season": "秋",
        "occasion": "轻户外",
        "palette": ["#4a5d46", "#2c3630", "#b7b2a3"],
        "items": [
            {"category": "外套", "name": "三层压胶冲锋衣", "color": "#4a5d46",
             "note": "肩部顺肩不收，宽度成为支撑面"},
            {"category": "内搭", "name": "美利奴长袖", "color": "#b7b2a3",
             "note": "薄而保暖，不占体量"},
            {"category": "下装", "name": "收脚软壳裤", "color": "#2c3630",
             "note": "裤脚收进鞋帮，腿线由鞋筒接住"},
            {"category": "鞋履", "name": "中帮登山鞋", "color": "#2c3630",
             "note": "与裤子同色，竖向一段补高度"},
            {"category": "配饰", "name": "宽檐帽", "color": "#b7b2a3",
             "note": "帽檐替肩部挡住视觉宽度"},
        ],
        "tags": ("broad", "heavy", "short-leg"),
        "reasons": {
            "broad": "硬壳肩部顺肩不收，宽阔的肩背成为装备的支撑面。",
            "heavy": "压胶面料有结构感，厚实体量被硬挺版型整理成直线。",
            "short-leg": "裤脚收进中帮鞋帮，竖向的鞋筒替腿补上高度。",
        },
        "tips": ["腰带收在自然腰线略高处，下摆不要压住胯",
                 "深色下装配深色鞋，减少腿部截断"],
        "avoid": "过长的下摆盖住臀部一半，会把短腿身比压得更明显。",
    },
    {
        "id": "quiet-yoga",
        "image_query": "瑜伽背心 高腰紧身裤 居家运动",
        "name": "静息舒展",
        "tagline": "轻的衣服，才听得见呼吸。",
        "story": "居家运动要的是不打扰：细肩带背心与高腰紧身裤贴合身体，外面罩一件开襟薄衫，"
                 "动作停下来的瞬间也能保持体面。轻体量穿贴身面料最自在，标准腿身比让高腰紧身裤"
                 "的比例刚好落在腰线之上。",
        "style": "运动",
        "season": "夏",
        "occasion": "居家运动",
        "palette": ["#cbbfae", "#6d7b70", "#f0ece5"],
        "items": [
            {"category": "上装", "name": "细肩带运动背心", "color": "#cbbfae",
             "note": "肩带落在肩点以内"},
            {"category": "外套", "name": "开襟薄衫", "color": "#f0ece5",
             "note": "敞开穿，动作时形成纵向线条"},
            {"category": "下装", "name": "高腰紧身裤", "color": "#6d7b70",
             "note": "停在标准腰线，比例不用调整"},
            {"category": "鞋履", "name": "赤足训练鞋", "color": "#f0ece5",
             "note": "薄底贴合，动作更稳"},
            {"category": "配饰", "name": "宽发带", "color": "#6d7b70",
             "note": "把视线提到面部，肩颈保持干净"},
        ],
        "tags": ("slim", "light", "balanced-leg"),
        "reasons": {
            "slim": "背心的细肩带落在肩点以内，窄肩线条更清晰。",
            "light": "贴身弹力面料最能体现轻体量，不必担心勒出痕迹。",
            "balanced-leg": "高腰紧身裤停在标准腰线，比例不需要额外调整。",
        },
        "tips": ["外层薄衫敞开穿，动作时自然形成纵向线条",
                 "上浅下深，视觉重心稳稳落回腰线"],
        "avoid": "宽松卫裤配长款罩衫，会把轻体量裹成一团、动作也变笨。",
    },
    {
        "id": "resort-drift",
        "image_query": "海岛度假 长裙 草编帽 凉鞋",
        "name": "海岛漂流",
        "tagline": "把风留在衣服里。",
        "story": "海边的衣服要留住风：宽摆长裙用薄棉裁成，腰间只系一条细带，走起来时裙摆自己"
                 "形成弧线。匀称身形穿长裙最省事，搭一件洗旧的白衬衫防晒，标准腿身比让裙长可以"
                 "安心落在脚踝上方。",
        "style": "度假",
        "season": "夏",
        "occasion": "海岛度假",
        "palette": ["#e8dcc8", "#8fb3a8", "#d8b48a"],
        "items": [
            {"category": "连衣裙", "name": "薄棉宽摆长裙", "color": "#d8b48a",
             "note": "腰间只系细带，裙摆留出弧线"},
            {"category": "外套", "name": "白色亚麻衬衫", "color": "#e8dcc8",
             "note": "当防晒外搭，长度停在腰线附近"},
            {"category": "鞋履", "name": "皮绳凉鞋", "color": "#8fb3a8",
             "note": "细带露出脚背，腿线不被切断"},
            {"category": "配饰", "name": "草编宽檐帽", "color": "#e8dcc8",
             "note": "帽檐替肩颈遮阳，也柔化轮廓"},
            {"category": "包袋", "name": "草编托特包", "color": "#d8b48a",
             "note": "粗编纹理呼应裙摆的起伏"},
        ],
        "tags": ("regular", "light", "balanced-leg"),
        "reasons": {
            "regular": "宽摆裙只收腰不强调肩，匀称比例穿得最松弛。",
            "light": "薄棉几乎没有重量，裙摆才会随走动自然起伏。",
            "balanced-leg": "标准腿身比配及踝长度刚刚好，不会被裙长吃掉。",
        },
        "tips": ["细腰带系在自然腰线，裙摆立刻有层次",
                 "衬衫当防晒外搭，长度停在腰线附近"],
        "avoid": "厚重的提花或硬衬裙摆，会把海边的松弛感一并压住。",
    },
    {
        "id": "sunset-wrap",
        "image_query": "缎面长裙 晚宴 披肩 高跟鞋",
        "name": "暮色包裹",
        "tagline": "黄昏的颜色，裹在身上。",
        "story": "度假晚宴不需要正式：一件斜裁的缎面长裙顺着身体落下，肩部只搭一条同色薄披肩，"
                 "走动时有光在面料上移动。厚实的体量正好撑住缎面的垂坠，偏长的腿身比让长裙落到"
                 "脚踝上方依然显高。",
        "style": "度假",
        "season": "夏",
        "occasion": "度假晚宴",
        "palette": ["#a8603f", "#e0b48f", "#5c3a30"],
        "items": [
            {"category": "连衣裙", "name": "斜裁缎面长裙", "color": "#a8603f",
             "note": "斜裁接缝落在胯线之外，顺着身体落下"},
            {"category": "外套", "name": "同色薄披肩", "color": "#e0b48f",
             "note": "搭在一侧肩头，露出另一侧肩线"},
            {"category": "鞋履", "name": "细带高跟凉鞋", "color": "#5c3a30",
             "note": "与裙同色系，长度感更连贯"},
            {"category": "配饰", "name": "金色耳环", "color": "#e0b48f",
             "note": "一点金属反光，替缎面收尾"},
            {"category": "包袋", "name": "硬壳小手袋", "color": "#5c3a30",
             "note": "小体量手袋不打断长裙的竖向线条"},
        ],
        "tags": ("regular", "heavy", "long-leg"),
        "reasons": {
            "regular": "斜裁不依赖肩部结构，匀称比例穿起来最顺。",
            "heavy": "缎面有垂坠也有分量，厚实体量正好把光泽撑开。",
            "long-leg": "偏长的腿身比让长裙落在脚踝上方仍然显高。",
        },
        "tips": ["披肩搭在一侧肩头，露出另一侧的肩线",
                 "鞋与裙同色系，长度感更连贯"],
        "avoid": "过紧的直筒长裙配平底鞋，会让斜裁的流动性完全消失。",
    },
    {
        "id": "retro-tweed",
        "image_query": "花呢外套 羊毛半裙 复古学院",
        "name": "复古花呢",
        "tagline": "老派的布料，新派的比例。",
        "story": "花呢的分量正好配宽阔的肩背：短款花呢外套顺肩直落，内搭高领与直筒半裙保持纵向"
                 "的安静。匀称的腿身比让半裙长度可以落在小腿肚，配一双系带短靴，坐下时布料有"
                 "厚度，站起来线条有分寸。",
        "style": "复古",
        "season": "冬",
        "occasion": "文艺聚会",
        "palette": ["#7a6a52", "#2f3b33", "#cbbfae"],
        "items": [
            {"category": "外套", "name": "短款花呢外套", "color": "#7a6a52",
             "note": "顺肩裁剪，停在胯骨上方"},
            {"category": "内搭", "name": "细针高领衫", "color": "#cbbfae",
             "note": "浅色领口提亮面部"},
            {"category": "下装", "name": "直筒羊毛半裙", "color": "#2f3b33",
             "note": "长度落在小腿肚，直筒不扩"},
            {"category": "鞋履", "name": "系带短靴", "color": "#2f3b33",
             "note": "与半裙同色，腿线延伸下去"},
            {"category": "配饰", "name": "珍珠耳钉", "color": "#cbbfae",
             "note": "小颗珍珠，与花呢的粗粝对照"},
        ],
        "tags": ("broad", "medium", "balanced-leg"),
        "reasons": {
            "broad": "花呢外套顺肩裁剪，宽阔肩背反而撑出老派的端正。",
            "medium": "粗花呢有骨架，中等体量穿起来挺括又不显厚。",
            "balanced-leg": "半裙停在标准腰线上，直筒版型顺着小腿自然落下。",
        },
        "tips": ["外套长度停在胯骨上方，避免压住半裙比例",
                 "内搭与半裙同色，纵向线条更清楚"],
        "avoid": "带大垫肩或肩章的花呢外套，会在最宽处再加一层结构。",
    },
    {
        "id": "seventies-line",
        "image_query": "七十年代 喇叭裤 麂皮夹克",
        "name": "七十年代",
        "tagline": "上窄下宽的年代。",
        "story": "窄肩上装配喇叭裤，是七十年代留下的比例公式：贴身针织衫勾勒出干净的肩线，高腰"
                 "喇叭裤从膝盖开始向外展开，偏长的腿身比让裤型一路延伸到鞋面。腰间系一条细皮带，"
                 "把年代感收在一个点上。",
        "style": "复古",
        "season": "秋",
        "occasion": "日常休闲",
        "palette": ["#b5713f", "#5c4a3a", "#e3d3b8"],
        "items": [
            {"category": "上装", "name": "贴身针织衫", "color": "#e3d3b8",
             "note": "塞进裤腰，肩线留给喇叭裤平衡"},
            {"category": "下装", "name": "高腰喇叭裤", "color": "#5c4a3a",
             "note": "从膝下展开，裤脚盖住鞋面"},
            {"category": "外套", "name": "麂皮短夹克", "color": "#b5713f",
             "note": "短款不压腰线，增加年代质感"},
            {"category": "鞋履", "name": "厚底木屐鞋", "color": "#5c4a3a",
             "note": "厚底接住喇叭裤的长度"},
            {"category": "配饰", "name": "细皮带", "color": "#b5713f",
             "note": "把年代感收在腰线这一点上"},
        ],
        "tags": ("slim", "medium", "long-leg"),
        "reasons": {
            "slim": "贴身针织把窄肩留给喇叭裤去平衡。",
            "medium": "中厚针织与麂皮都有分量，中等体量穿出年代感。",
            "long-leg": "高腰喇叭裤从膝下展开，偏长的腿线一路延伸到底。",
        },
        "tips": ["针织衫塞进裤腰，腰线交给细皮带强调",
                 "喇叭裤长度盖住鞋面，比例更完整"],
        "avoid": "宽松上衣配喇叭裤，上下同时展开会让轮廓失去重点。",
    },
    {
        "id": "clean-column",
        "image_query": "极简 同色系 长衬衫 阔腿裤",
        "name": "纯净立柱",
        "tagline": "一根竖直的线，就够了。",
        "story": "少即是清楚的轮廓：直身长衬衫配同色阔腿裤，用同一块面料从上到下拉出一条不带"
                 "断点的竖线。厚实体量在这种穿法里变成安稳的存在感，腰间用一条极细的腰带分出"
                 "比例，其余全部交给面料的垂坠。",
        "style": "极简",
        "season": "四季",
        "occasion": "通勤办公",
        "palette": ["#e6e2da", "#3c3f42", "#a9a49b"],
        "items": [
            {"category": "上装", "name": "直身长衬衫", "color": "#e6e2da",
             "note": "无领直身，不改造肩线"},
            {"category": "下装", "name": "同色阔腿裤", "color": "#e6e2da",
             "note": "与衬衫同料，裤脚垂到鞋面"},
            {"category": "外套", "name": "长款无领外套", "color": "#a9a49b",
             "note": "敞开穿，两条竖线夹出一条中线"},
            {"category": "鞋履", "name": "方头平底鞋", "color": "#3c3f42",
             "note": "深色收底，让立柱有落点"},
            {"category": "配饰", "name": "极细腰带", "color": "#3c3f42",
             "note": "只做一次比例分割，不加装饰"},
        ],
        "tags": ("regular", "heavy", "balanced-leg"),
        "reasons": {
            "regular": "无领直身版型不改造肩线，匀称比例保持干净。",
            "heavy": "同色同料的垂坠把厚实体量收进一条直线，不做横向分割。",
            "balanced-leg": "阔腿裤落在标准腰线上，裤脚垂到鞋面形成连续。",
        },
        "tips": ["上下同色同料，用面料差异代替图案",
                 "外套敞开穿，两条竖线夹出一条中线"],
        "avoid": "短款上衣配横向拼接的裤子，会把立柱切成两段。",
    },
    {
        "id": "campus-preppy",
        "image_query": "学院风 针织背心 百褶裙 乐福鞋",
        "name": "学院预科",
        "tagline": "把衬衫穿得像春天。",
        "story": "窄肩身形穿学院风最合身：牛津衬衫的肩线刚好落在肩点，外面套一件 V 领针织背心，"
                 "中厚针距给身体补上一点分量。下身是及膝百褶裙与乐福鞋，标准腿身比让裙长停在"
                 "膝盖上方一点，走路时褶线跟着动。",
        "style": "学院",
        "season": "春",
        "occasion": "校园日常",
        "palette": ["#c3d3e0", "#2e3a54", "#e9e4d8"],
        "items": [
            {"category": "上装", "name": "牛津纺衬衫", "color": "#e9e4d8",
             "note": "肩线正落在肩点，轮廓干净"},
            {"category": "内搭", "name": "V 领针织背心", "color": "#2e3a54",
             "note": "中厚针距补一点分量，长度停在腰线"},
            {"category": "下装", "name": "及膝百褶裙", "color": "#c3d3e0",
             "note": "裙长停在膝上一点，褶线随走动打开"},
            {"category": "鞋履", "name": "流苏乐福鞋", "color": "#2e3a54",
             "note": "与背心同色，上下呼应"},
            {"category": "配饰", "name": "条纹领带", "color": "#c3d3e0",
             "note": "细条纹压在背心下，露出领口一段"},
        ],
        "tags": ("slim", "medium", "balanced-leg"),
        "reasons": {
            "slim": "衬衫肩线正落在肩点，窄肩穿出干净的学院轮廓。",
            "medium": "中厚背心叠在衬衫外补上一点分量，也不会显臃肿。",
            "balanced-leg": "百褶裙落在标准腰线，裙长停在膝上一点。",
        },
        "tips": ["背心长度停在腰线，衬衫下摆可以露出两指",
                 "袜与鞋同色，腿部线条不会被截断"],
        "avoid": "过大的落肩卫衣配长裙，会把学院风的整洁感冲散。",
    },
)


def analyze_glb(path: Path | None) -> dict[str, Any] | None:
    """Estimate body descriptors from a GLB mesh, or ``None`` when unusable.

    Returns the stature, the bounding box spans, the 20-band silhouette
    profile, the derived descriptors, the three tag families with their Chinese
    labels plus 4-6 display metrics.  Thresholds and band definitions are in the
    module docstring.

    ``None`` means "no usable model" and is returned for a missing or unreadable
    path, an oversized file, a truncated header or chunk, a non-float or
    non-VEC3 POSITION accessor, sparse or Draco geometry, an accessor outside
    the BIN chunk, too many declared vertices, an external buffer, a degenerate
    (flat or empty) mesh and any unexpected parsing error.  Callers then answer
    with the generic catalogue.
    """
    if path is None:
        return None
    try:
        positions = _read_positions(path)
        if positions is None or len(positions) < MIN_VERTICES:
            return None
        return _describe(positions)
    except Exception:
        # The recommendation endpoint must always answer; a broken model file
        # degrades to the generic catalogue instead of surfacing an error.
        return None


def _read_positions(path: Path) -> np.ndarray | None:
    """Decode every POSITION accessor of a GLB into one (N, 3) float array."""
    if not path.is_file():
        return None
    size = path.stat().st_size
    if size <= 0 or size > MAX_GLB_BYTES:
        return None
    with path.open("rb") as handle:
        header = handle.read(VEC3_BYTES)
        if len(header) != VEC3_BYTES:
            return None
        magic, version, declared = struct.unpack("<4sII", header)
        if magic != GLB_MAGIC or version != GLB_VERSION or declared > size:
            return None
        document = None
        binary = None
        cursor = VEC3_BYTES
        while cursor + 8 <= min(declared, size):
            chunk_header = handle.read(8)
            if len(chunk_header) != 8:
                return None
            length, kind = struct.unpack("<II", chunk_header)
            cursor += 8
            if length == 0 or cursor + length > size:
                return None
            payload = handle.read(length)
            if len(payload) != length:
                return None
            cursor += length
            if kind == JSON_CHUNK and document is None:
                document = payload
            elif kind == BIN_CHUNK and binary is None:
                binary = payload
    if document is None or binary is None:
        return None
    try:
        gltf = json.loads(document.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(gltf, dict):
        return None
    return _decode_meshes(gltf, binary)


def _decode_meshes(gltf: dict, binary: bytes) -> np.ndarray | None:
    """Collect and decode the POSITION accessors referenced by the meshes."""
    accessors = gltf.get("accessors")
    views = gltf.get("bufferViews")
    buffers = gltf.get("buffers")
    meshes = gltf.get("meshes")
    if not all(isinstance(item, list) for item in (accessors, views, buffers, meshes)):
        return None
    if not buffers or isinstance(buffers[0], dict) and "uri" in buffers[0]:
        return None
    indices: list[int] = []
    for mesh in meshes:
        if not isinstance(mesh, dict):
            continue
        if _has_draco(mesh.get("extensions")):
            return None
        primitives = mesh.get("primitives")
        if not isinstance(primitives, list):
            continue
        for primitive in primitives:
            if not isinstance(primitive, dict):
                continue
            if _has_draco(primitive.get("extensions")):
                return None
            attributes = primitive.get("attributes")
            index = attributes.get("POSITION") if isinstance(attributes, dict) else None
            if isinstance(index, int) and 0 <= index < len(accessors):
                indices.append(index)
    if not indices:
        return None
    total = 0
    for index in indices:
        accessor = accessors[index]
        if not isinstance(accessor, dict):
            return None
        if accessor.get("componentType") != FLOAT_COMPONENT or accessor.get("type") != "VEC3":
            return None
        if "sparse" in accessor:
            return None
        count = accessor.get("count")
        if not isinstance(count, int) or count <= 0:
            return None
        total += count
    if total > MAX_VERTICES:
        return None
    chunks = []
    for index in indices:
        array = _decode_accessor(accessors[index], views, binary)
        if array is None:
            return None
        chunks.append(array)
    return np.concatenate(chunks, axis=0)


def _has_draco(extensions: Any) -> bool:
    return isinstance(extensions, dict) and DRACO_EXTENSION in extensions


def _decode_accessor(accessor: dict, views: list, binary: bytes) -> np.ndarray | None:
    """Read one float32 VEC3 accessor out of the BIN chunk in a single step."""
    view_index = accessor.get("bufferView")
    if not isinstance(view_index, int) or not 0 <= view_index < len(views):
        return None
    view = views[view_index]
    if not isinstance(view, dict) or view.get("buffer", 0) != 0:
        return None
    count = accessor["count"]
    offset = _as_offset(view.get("byteOffset")) + _as_offset(accessor.get("byteOffset"))
    stride = view.get("byteStride") or VEC3_BYTES
    if not isinstance(stride, int) or stride < VEC3_BYTES or stride % 4:
        return None
    end = offset + stride * (count - 1) + VEC3_BYTES
    if offset < 0 or end > len(binary):
        return None
    view_3d = np.ndarray(
        shape=(count, 3),
        dtype="<f4",
        buffer=binary,
        offset=offset,
        strides=(stride, 4),
    )
    return np.array(view_3d, dtype=np.float32)


def _as_offset(value: Any) -> int:
    return value if isinstance(value, int) and value >= 0 else 0


def _describe(positions: np.ndarray) -> dict[str, Any] | None:
    """Turn decoded vertices into the descriptors, tags, metrics and notes."""
    y = positions[:, 1]
    top = float(y.max())
    stature = top - float(y.min())
    if not math.isfinite(stature) or stature <= 0:
        return None
    width = float(positions[:, 0].max() - positions[:, 0].min())
    depth = float(positions[:, 2].max() - positions[:, 2].min())
    profile = _silhouette(positions, top, stature)
    if not all(math.isfinite(value) for value in profile):
        return None

    torso = float(np.median(profile[TORSO_BANDS[0]:TORSO_BANDS[1]]))
    spike_limit = torso * ARM_SPIKE_RATIO if torso > 0 else None
    shoulder, shoulder_band, arms_removed = _window_max(profile, SHOULDER_BANDS, spike_limit)
    waist = min(profile[WAIST_BANDS[0]:WAIST_BANDS[1]])
    hip, hip_band, _ = _window_max(profile, HIP_BANDS, None)
    hip_line = (hip_band + 0.5) / PROFILE_BANDS
    leg_ratio = 1.0 - hip_line
    thickness = depth / stature
    depth_ratio = depth / width if width > 0 else 0.0
    waist_ratio = waist / shoulder if shoulder > 0 else 0.0

    build = _bucket(shoulder, BUILD_THRESHOLDS, TAG_FAMILIES["build"])
    volume = _bucket(thickness, THICKNESS_THRESHOLDS, TAG_FAMILIES["volume"])
    legs = _bucket(leg_ratio, LEG_THRESHOLDS, TAG_FAMILIES["legs"])
    hints = {
        "build": BUILD_HINTS[build],
        "volume": VOLUME_HINTS[volume],
        "legs": LEG_HINTS[legs],
    }
    metrics = [
        {"label": "身高 / 肩宽", "value": _value(1 / shoulder if shoulder > 0 else None),
         "hint": hints["build"]},
        {"label": "腰宽 / 肩宽", "value": _value(waist_ratio), "hint": _waist_hint(waist_ratio)},
        {"label": "胯宽 / 身高", "value": _value(hip), "hint": _hip_hint(hip, shoulder)},
        {"label": "腿长 / 身高", "value": _value(leg_ratio), "hint": hints["legs"]},
        {"label": "体厚 / 身高", "value": _value(thickness), "hint": hints["volume"]},
        {"label": "体厚 / 体宽", "value": _value(depth_ratio),
         "hint": "前后厚度与左右宽度的比值，受姿态与包围盒影响，仅作版型参考"},
    ]
    notes = [
        "轮廓按身体高度等分为 20 段，数值为该段最大横向跨度相对身高的归一化值。",
        "第 1 段从模型顶部（头顶）起算并依次向下，最后一段为足部。",
        "指标由三维模型包围盒与轮廓切片估算，不是人体测量学结论，仅作版型参考。",
    ]
    if arms_removed:
        notes.append("肩部区间的最大跨度已剔除明显更宽的片段（双臂水平展开），"
                     "肩宽取手腕以外的躯干宽度。")
    elif shoulder_band == SHOULDER_BANDS[0] and shoulder > 0:
        notes.append("肩部指标取肩线附近最宽的一段，双臂张开时该数值会包含手臂。")
    return {
        "height": stature,
        "width": width,
        "depth": depth,
        "profile": profile,
        "shoulder_ratio": shoulder,
        "waist_ratio": waist_ratio,
        "hip_ratio": hip,
        "leg_ratio": leg_ratio,
        "thickness": thickness,
        "depth_ratio": depth_ratio,
        "tags": {"build": build, "volume": volume, "legs": legs},
        "labels": {
            "build": BUILD_LABELS[build],
            "volume": VOLUME_LABELS[volume],
            "legs": LEG_LABELS[legs],
        },
        "metrics": metrics,
        "notes": notes,
    }


def _silhouette(positions: np.ndarray, top: float, stature: float) -> list[float]:
    """Largest X span of every band, as a fraction of the stature."""
    from_top = (top - positions[:, 1]) / stature
    bands = np.clip((from_top * PROFILE_BANDS).astype(np.int64), 0, PROFILE_BANDS - 1)
    x = positions[:, 0]
    order = np.argsort(bands, kind="stable")
    ordered = bands[order]
    profile = []
    for band in range(PROFILE_BANDS):
        start = int(np.searchsorted(ordered, band, side="left"))
        stop = int(np.searchsorted(ordered, band, side="right"))
        if stop <= start:
            profile.append(0.0)
            continue
        values = x[order[start:stop]]
        profile.append((float(values.max()) - float(values.min())) / stature)
    return profile


def _window_max(
    profile: list[float], window: tuple[int, int], spike_limit: float | None
) -> tuple[float, int, bool]:
    """Widest band in ``window``; optionally skip arm-wide bands first."""
    candidates = list(range(*window))
    kept = candidates
    if spike_limit is not None:
        kept = [band for band in candidates if profile[band] <= spike_limit]
    if not kept:
        kept = candidates
    best = max(kept, key=lambda band: profile[band])
    return profile[best], best, kept != candidates


def _bucket(value: float, thresholds: tuple[float, float], names: tuple[str, ...]) -> str:
    if value < thresholds[0]:
        return names[0]
    if value < thresholds[1]:
        return names[1]
    return names[2]


def _value(number: float | None) -> str:
    return "—" if number is None else f"{number:.2f}"


def _waist_hint(waist_ratio: float) -> str:
    if waist_ratio and waist_ratio < 0.72:
        return "腰线明显，收腰结构或高腰下装都会更出效果"
    if waist_ratio and waist_ratio < 0.85:
        return "腰线平缓，用腰带或结构线制造分界"
    return "腰线不明显，可用高腰或收腰结构制造比例"


def _hip_hint(hip_ratio: float, shoulder_ratio: float) -> str:
    if hip_ratio > shoulder_ratio:
        return "胯部宽于肩部，A 字与直筒下装更顺"
    return "胯部窄于肩部，百褶或宽腿裤能补足下盘"


def analysis_document(
    geometry: dict[str, Any] | None,
    *,
    pose_mode: str | None = None,
    reference_size: tuple[int, int] | None = None,
) -> dict[str, Any]:
    """Build the ``analysis`` object of the response, available or not."""
    if not geometry:
        return {
            "available": False,
            "method": METHOD,
            "labels": {},
            "metrics": [],
            "profile": None,
            "notes": ["尚未生成三维模型，已按通用体型推荐"],
            "tags": [],
            "ratios": None,
            "tag_families": {},
        }
    labels = dict(geometry["labels"])
    labels["pose"] = POSE_LABELS.get(pose_mode or "", POSE_UNRECORDED)
    notes = list(geometry["notes"])
    if pose_mode not in POSE_LABELS:
        notes.append("任务未记录姿态，指标按常规站姿解读。")
    if reference_size:
        notes.append(
            f"参考图 {reference_size[0]}×{reference_size[1]} 像素，仅作输入记录，"
            "不参与体型计算。"
        )
    return {
        "available": True,
        "method": METHOD,
        "labels": labels,
        "metrics": [dict(metric) for metric in geometry["metrics"]],
        "profile": [round(value, 3) for value in geometry["profile"]],
        "notes": notes,
        "tags": [
            geometry["tags"]["build"],
            geometry["tags"]["volume"],
            geometry["tags"]["legs"],
        ],
        # Machine-readable form of the same numbers, for the size matching
        # library: it needs the ratios themselves and the tag families, not the
        # display strings above.
        "ratios": {
            "shoulder_ratio": geometry["shoulder_ratio"],
            "waist_ratio": geometry["waist_ratio"],
            "hip_ratio": geometry["hip_ratio"],
            "leg_ratio": geometry["leg_ratio"],
            "thickness_ratio": geometry["thickness"],
        },
        "tag_families": dict(geometry["tags"]),
    }


def catalog_filters() -> dict[str, list[dict[str, Any]]]:
    """Counts per filter value over the whole catalogue, unfiltered.

    Values are ordered by count (descending) and then by value, so a client can
    render the filter list without re-sorting it.
    """
    return {
        "styles": _counts("style"),
        "seasons": _counts("season"),
        "occasions": _counts("occasion"),
    }


def _counts(field: str) -> list[dict[str, Any]]:
    counted = Counter(outfit[field] for outfit in CATALOG)
    ordered = sorted(counted.items(), key=lambda item: (-item[1], item[0]))
    return [{"id": value, "count": count} for value, count in ordered]


def recommend_outfits(
    geometry: dict[str, Any] | None,
    *,
    style: str | None = None,
    season: str | None = None,
    occasion: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[dict[str, Any]]:
    """Score, order and trim the catalogue.  See the module docstring."""
    tags = geometry["tags"] if geometry else {}
    wanted = {"style": style, "season": season, "occasion": occasion}
    scored = []
    for index, outfit in enumerate(CATALOG):
        score, matched = _score(outfit, tags, wanted)
        scored.append((score, index, outfit, matched))
    # Equal scores keep catalogue order, so the editorial sequence survives.
    scored.sort(key=lambda entry: (-entry[0], entry[1]))
    return [
        _public_outfit(outfit, score, matched, tags)
        for score, _index, outfit, matched in scored[:limit]
    ]


def _score(
    outfit: dict[str, Any], tags: dict[str, str], wanted: dict[str, str | None]
) -> tuple[float, list[str]]:
    score = SCORE_BASE
    matched = []
    for family, value in tags.items():
        if value and value in outfit["tags"]:
            score += TAG_WEIGHTS[family]
            matched.append(value)
    for family, value in wanted.items():
        if not value:
            continue
        if outfit[family] == value:
            score += FILTER_WEIGHTS[family]
            matched.append(value)
        else:
            score -= FILTER_PENALTIES[family]
    return round(score, 2), matched


def _public_outfit(
    outfit: dict[str, Any], score: float, matched: list[str], tags: dict[str, str]
) -> dict[str, Any]:
    return {
        "id": outfit["id"],
        "name": outfit["name"],
        "tagline": outfit["tagline"],
        "story": outfit["story"],
        "style": outfit["style"],
        "season": outfit["season"],
        "occasion": outfit["occasion"],
        "palette": list(outfit["palette"]),
        "items": [dict(item) for item in outfit["items"]],
        "tips": list(outfit["tips"]),
        "avoid": outfit["avoid"],
        "reason": _reason(outfit, tags),
        "score": score,
        "matched": matched,
    }


def _reason(outfit: dict[str, Any], tags: dict[str, str]) -> str:
    """Join the fitting body reasons, the skeleton family first."""
    hits = [
        tags[family]
        for family in ("build", "volume", "legs")
        if tags.get(family) in outfit["tags"]
    ]
    if hits:
        return " ".join(outfit["reasons"][hit] for hit in hits[:2])
    return f"未生成三维模型，按通用体型推荐：{outfit['tagline']}"


def outfit_report(
    store: Store,
    *,
    job_id: str | None = None,
    asset_id: str | None = None,
    style: str | None = None,
    season: str | None = None,
    occasion: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> dict[str, Any]:
    """Assemble the whole ``GET /api/outfits`` body.

    An explicit ``asset_id`` wins over the mesh a ``job_id`` would supply, while
    the job still provides the pose label and the reference image note.  Any
    unknown, unreadable or unsupported model yields ``source: "default"``
    instead of an error.
    """
    path, pose_mode, reference_size = _resolve(store, job_id, asset_id)
    geometry = analyze_glb(path)
    return {
        "source": "model" if geometry else "default",
        "analysis": analysis_document(
            geometry, pose_mode=pose_mode, reference_size=reference_size
        ),
        "filters": catalog_filters(),
        "recommendations": recommend_outfits(
            geometry, style=style, season=season, occasion=occasion, limit=limit
        ),
    }


def _resolve(
    store: Store, job_id: str | None, asset_id: str | None
) -> tuple[Path | None, str | None, tuple[int, int] | None]:
    """Find the GLB to analyse plus its pose label and reference image size."""
    pose_mode = None
    reference_size = None
    path = None
    try:
        job = store.job(job_id) if job_id else None
        if job:
            pose_mode = _pose_mode(job)
            reference_size = _reference_size(store, job)
        if asset_id:
            path = _asset_mesh(store, asset_id)
        if path is None and job:
            path = _job_mesh(store, job)
    except Exception:
        return None, pose_mode, reference_size
    return path, pose_mode, reference_size


def _pose_mode(job: dict[str, Any]) -> str | None:
    request = job.get("request")
    mode = request.get("pose_mode") if isinstance(request, dict) else None
    return mode if mode in POSE_LABELS else None


def _reference_size(store: Store, job: dict[str, Any]) -> tuple[int, int] | None:
    request = job.get("request")
    front = request.get("front") if isinstance(request, dict) else None
    asset = store.asset(front) if isinstance(front, str) else None
    if not asset:
        return None
    width, height = asset.get("width"), asset.get("height")
    if isinstance(width, int) and isinstance(height, int) and width > 0 and height > 0:
        return width, height
    return None


def _job_mesh(store: Store, job: dict[str, Any]) -> Path | None:
    """Latest GLB artifact of the job, newest first."""
    artifacts = job.get("artifacts")
    if not isinstance(artifacts, list):
        return None
    for artifact in reversed(artifacts):
        if isinstance(artifact, dict) and artifact.get("format") == "GLB":
            path = _asset_mesh(store, artifact.get("asset_id"))
            if path is not None:
                return path
    return None


def _asset_mesh(store: Store, asset_id: Any) -> Path | None:
    if not isinstance(asset_id, str):
        return None
    asset = store.asset(asset_id)
    if not asset or asset.get("format") != "GLB":
        return None
    path = store.path(asset_id)
    return path if path.is_file() else None
