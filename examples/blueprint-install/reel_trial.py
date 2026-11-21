"""Build the interview sample against synthetic media. All external responses are fixtures.

Usage: python reel_trial.py BLUEPRINT.zip NEW_TEST_WORKSPACE
Requires flow_base, routinekeel-blueprint-media and ffmpeg/ffprobe on PATH.
"""

import argparse
import json
import math
import struct
import subprocess
import wave
from pathlib import Path

from flow_base.core.service import WorkflowService


def program(body):
    lines = ["c = json_loads(text)", *body.splitlines()]
    return "def transform(text, params):\n" + "\n".join("    " + line for line in lines)


def ret(expression):
    return program("return json_dumps(" + expression + ")")


def make_fixtures(root):
    folder = root / "fixtures"
    folder.mkdir(parents=True)
    (folder / "script.txt").write_text("Hello.World.", encoding="utf-8")
    for name, hz in [("lead", 330), ("guest", 660)]:
        with wave.open(str(folder / (name + ".wav")), "wb") as audio:
            audio.setparams((1, 2, 24000, 0, "NONE", ""))
            audio.writeframes(
                b"".join(
                    struct.pack(
                        "<h", round(12000 * math.sin(2 * math.pi * hz * i / 24000))
                    )
                    for i in range(12000)
                )
            )
    for name, args in [
        ("cover.png", ["-frames:v", "1"]),
        ("fixture.mp4", ["-t", "1", "-pix_fmt", "yuv420p"]),
    ]:
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=blue:size=160x240:rate=25",
                *args,
                str(folder / name),
            ],
            check=True,
            timeout=30,
        )


def file(name):
    return {"$file": "fixtures/" + name}


def example(context, expected, expected_arguments=None):
    return {
        "context": context,
        "expected": expected,
        "expected_arguments": expected_arguments,
    }


def resolution(draft, manual=False):
    steps = {}

    def host(s, cap, **kwargs):
        steps[s] = {"mode": "host", "capability": cap, **kwargs}

    def local(s, result, examples, **kwargs):
        steps[s] = {"mode": "local", "result": result, "examples": examples, **kwargs}

    host(
        "前置",
        "character_profile",
        reuse_approved=True,
        profile_config=["character"],
        asset_fields=[["角色設定檔", "定裝照"]],
    )
    host(
        "1",
        "text",
        text_inputs={"script": ["已審腳本", "檔案"]},
        assertion=ret(
            "''.join([s['台詞'] for s in c['outputs']['台詞段落表']]) == c['texts']['script']"
        ),
    )
    host("2", "speech_synthesis", asset_fields=[["非主角配音", "*", "音檔"]])
    # The host returns only the protagonist clips. C4 constructs real silence from measured guest clips.
    args3 = program("""rows = []
for line in c['inputs']['台詞段落表']:
    role = 'lead'
    audio = c['answer']['lead']
    if line['說話者'] == '非主角':
        role = 'other'
        audio = [g['音檔'] for g in c['inputs']['非主角配音'] if g['順序'] == line['順序']][0]
    else:
        audio = audio[str(line['順序'])]
    rows.append({'role':role, 'text':line['台詞'], 'audio':audio})
return json_dumps({'segments':rows})""")
    res3 = program("""r = c['result']
rows = []
for i, seg in enumerate(r['timeline']):
    kind = '主角說話'
    if seg['role'] == 'other':
        kind = '空檔'
    rows.append({'順序':i+1, '類型':kind, '起':seg['start'], '迄':seg['end']})
return json_dumps({'主角配音':{'音檔':r['lead'], '副軌':r['other'], '總秒數':r['seconds'], '時間軸':rows, 'segments':r['timeline']}})""")
    examples3, examples7, examples8 = [], [], []
    for duration in (1, 2):
        seg = {"role": "lead", "start": 0, "end": duration, "text": "A"}
        main = {
            "音檔": "l",
            "副軌": "o",
            "總秒數": duration,
            "時間軸": [{"順序": 1, "類型": "主角說話", "起": 0, "迄": duration}],
            "segments": [seg],
        }
        examples3.append(
            example(
                {
                    "inputs": {
                        "台詞段落表": [{"順序": 1, "說話者": "主角", "台詞": "A"}]
                    },
                    "answer": {"lead": {"1": "voice"}},
                    "result": {
                        "lead": "l",
                        "other": "o",
                        "seconds": duration,
                        "timeline": [seg],
                    },
                },
                {"主角配音": main},
                {"segments": [{"role": "lead", "text": "A", "audio": "voice"}]},
            )
        )
        examples7.append(
            example(
                {
                    "inputs": {"主角配音": main},
                    "config": {"line_limit": 15, "split": "proportional"},
                    "result": {"captions": "s", "cues": [seg]},
                },
                {
                    "字幕": [
                        {"順序": 1, "起": 0, "迄": duration, "文字": "A", "字幕檔": "s"}
                    ]
                },
                {"timeline": [seg], "line_limit": 15, "split": "proportional"},
            )
        )
        args = {
            "width": 160,
            "height": 240,
            "max_seconds": 10,
            "cover_seconds": duration,
        }
        examples8.append(
            example(
                {
                    "config": args,
                    "inputs": {
                        "主角配音": main,
                        "字幕": [{"字幕檔": "s"}],
                        "背景音樂": {"是否使用": False},
                        "對嘴影片": {"影片檔": "v"},
                        "封面禎": {"圖檔": "p"},
                    },
                    "result": {
                        "file": "f",
                        "seconds": duration,
                        "width": 160,
                        "height": 240,
                    },
                },
                {"成片": {"影片檔": "f", "秒數": duration, "寬": 160, "高": 240}},
                {
                    **args,
                    "video": "v",
                    "cover": "p",
                    "lead": "l",
                    "other": "o",
                    "captions": "s",
                },
            )
        )
    host(
        "3",
        "speech_synthesis",
        operation="audio.timeline",
        arguments=args3,
        result=res3,
        examples=examples3,
    )
    host("4", "image_generation", asset_fields=[["封面禎", "圖檔"], ["首禎", "圖檔"]])
    host(
        "5",
        "lip_sync_video",
        asset_fields=[["對嘴影片", "影片檔"]],
        assertion=ret(
            "abs(c['outputs']['對嘴影片']['秒數'] - c['inputs']['主角配音']['總秒數']) <= 0.12"
        ),
    )
    res6 = ret("{'背景音樂':{'是否使用':False,'判定理由':c['config']['music_reason']}}")
    local(
        "6",
        res6,
        [
            example(
                {"config": {"music_reason": v}},
                {"背景音樂": {"是否使用": False, "判定理由": v}},
            )
            for v in ("本案例無配樂", "保留對話原音")
        ],
    )
    local(
        "7",
        ret(
            "{'字幕':[{'順序':i+1,'起':s['start'],'迄':s['end'],'文字':s['text'],'字幕檔':c['result']['captions']} for i,s in enumerate(c['result']['cues'])]}"
        ),
        examples7,
        operation="subtitle.srt",
        arguments=ret(
            "{'timeline':c['inputs']['主角配音']['segments'],'line_limit':c['config']['line_limit'],'split':c['config']['split']}"
        ),
    )
    args8 = program("""i=c['inputs']
k=c['config']
v={'width':k['width'],'height':k['height'],'max_seconds':k['max_seconds'],'cover_seconds':k['cover_seconds'],'captions':i['字幕'][0]['字幕檔'],'lead':i['主角配音']['音檔'],'other':i['主角配音']['副軌'],'video':i['對嘴影片']['影片檔'],'cover':i['封面禎']['圖檔']}
if i['背景音樂']['是否使用']:
    v['music']=i['背景音樂']['音檔']
return json_dumps(v)""")
    local(
        "8",
        ret(
            "{'成片':{'影片檔':c['result']['file'],'秒數':c['result']['seconds'],'寬':c['result']['width'],'高':c['result']['height']}}"
        ),
        examples8,
        operation="video.compose",
        arguments=args8,
        extra_inputs=["主角配音"],
        rationale="步驟 8 合約加上步驟 3 時間軸及雙軌；依 reel-composition/SKILL.md 合成。",
    )
    host("9", "text")
    steps["10"] = {
        "mode": "human",
        "extra_inputs": ["發布時間"],
        "rationale": "將步驟 11 的發布時間與成片、文案一同呈給步驟 10 審核。",
        "assertion": ret(
            "c['outputs']['審核結果']['結果'] == '通過' and c['outputs']['審核結果']['對象'] == '成片與文案'"
        ),
    }
    steps["11"] = {
        "mode": "external",
        "capability": "page_publishing",
        "assertion": ret(
            "c['inputs']['審核結果']['結果'] == '通過' and c['outputs']['發布結果']['貼文連結'].startswith('https://')"
        ),
    }
    res12 = ret(
        "{'發布紀錄':{'貼文連結':c['inputs']['發布結果']['貼文連結'],'實際發布時間':c['inputs']['發布結果']['實際發布時間'],'角色名稱':c['config']['character']}}"
    )
    result = {
        "貼文連結": "https://example.test/p/fixture",
        "實際發布時間": "2027-01-01T12:00:00Z",
    }
    local(
        "12",
        res12,
        [
            example(
                {"inputs": {"發布結果": result}, "config": {"character": name}},
                {"發布紀錄": {**result, "角色名稱": name}},
            )
            for name in ("A", "B")
        ],
    )
    bindings = {
        k: {
            "tool": v["name"],
            "version": "fixture-1",
            "kind": "fixture",
            "mapping": {"inputs": "fixture"},
            "evidence": "offline fixture only",
        }
        for k, v in draft["dependencies"].items()
    }
    bindings["text"] = {**bindings["character_profile"], "tool": "fixture text"}
    bindings["local_programs"].update(
        kind="local_probe",
        executables=["ffmpeg", "ffprobe"],
        evidence="local ffmpeg/ffprobe required and exercised by the trial",
    )
    bindings["music_generation"]["kind"] = "optional_disabled"
    decisions = {
        "contracts": "synthetic trial only",
        "music_generation": "本案例不加配樂",
        "split": "按字數比例",
        "cover_seconds": "0.4 秒測試值",
        "delivery_mode": "模擬平台回執",
    }
    recipe = {
        "steps": steps,
        "bindings": bindings,
        "decisions": decisions,
        "gaps": {
            k: v["need"] + " → 試做步驟 " + ",".join(v["used_by"])
            for k, v in draft["gaps"].items()
        },
    }
    if manual:
        del steps["12"]
        recipe.update(
            terminal_step="11",
            final_outputs=["成片", "貼文文案", "待發布內容"],
            handoff_contracts={
                "待發布內容": {
                    "schema": {"type": "object", "required": ["文案", "發布時間"]}
                }
            },
        )
        decisions.update(delivery_mode="人工上傳", page_publishing="交付待發布內容")
        bindings["page_publishing"]["kind"] = "manual"
        example_rows = [
            example(
                {
                    "inputs": {
                        "貼文文案": {"文案": v},
                        "發布時間": result["實際發布時間"],
                    }
                },
                {"待發布內容": {"文案": v, "發布時間": result["實際發布時間"]}},
            )
            for v in ("A", "B")
        ]
        steps["11"] = {
            "mode": "handoff",
            "rationale": "使用者選擇人工上傳模式，待發布不產生發布回執",
            "result": ret(
                "{'待發布內容':{'文案':c['inputs']['貼文文案']['文案'],'發布時間':c['inputs']['發布時間']}}"
            ),
            "examples": example_rows,
        }
    return recipe


def case(manual=False):
    posted = "2027-01-01T12:00:00Z"
    copy_text = {"文案": "Trial", "hashtag": ["#test"]}

    def reply(step, outputs=None, kind="host"):
        return {"step": step, "kind": kind, "outputs": outputs or {}}

    replies = [
        reply(
            "前置",
            {
                "角色設定檔": {
                    "角色名稱": "Trial",
                    "定裝照": file("cover.png"),
                    "人設文字": "trial",
                    "主角音色": "tone",
                }
            },
        ),
        reply("前置", kind="review"),
        reply(
            "1",
            {
                "台詞段落表": [
                    {"順序": 1, "說話者": "主角", "台詞": "Hello."},
                    {"順序": 2, "說話者": "非主角", "台詞": "World."},
                ]
            },
        ),
        reply(
            "2",
            {
                "非主角配音": [
                    {
                        "順序": 2,
                        "音檔": file("guest.wav"),
                        "秒數": 0.5,
                        "音色": "tone2",
                        "判定理由": "test",
                    }
                ]
            },
        ),
        reply("3", {"lead": {"1": file("lead.wav")}}),
        reply(
            "4",
            {
                "封面禎": {"圖檔": file("cover.png"), "標題": "Trial"},
                "首禎": {"圖檔": file("cover.png")},
            },
        ),
        reply("4", kind="review"),
        reply("5", {"對嘴影片": {"影片檔": file("fixture.mp4"), "秒數": 1}}),
        reply("9", {"貼文文案": copy_text}),
        reply("10", {"審核結果": {"對象": "成片與文案", "結果": "通過"}}, "human"),
    ]
    expected = {
        "成片": {"影片檔": {"$artifact": True}, "寬": 160, "高": 240},
        "貼文文案": copy_text,
    }
    if manual:
        expected["待發布內容"] = {"文案": "Trial", "發布時間": posted}
    else:
        result = {"貼文連結": "https://example.test/p/fixture", "實際發布時間": posted}
        replies.append(reply("11", {"發布結果": result}, "external"))
        expected.update(發布結果=result, 發布紀錄={**result, "角色名稱": "Trial"})
    return {
        "inputs": {
            "外觀照片": file("cover.png"),
            "人設文字": "trial",
            "聲音樣本": file("lead.wav"),
            "已審腳本": {"檔案": file("script.txt")},
            "製作重點與方向提醒": "fixture",
            "發布時間": posted,
        },
        "config": {
            "character": "Trial",
            "line_limit": 15,
            "split": "proportional",
            "width": 160,
            "height": 240,
            "max_seconds": 10,
            "cover_seconds": 0.4,
            "music_reason": "本案例無配樂",
        },
        "replies": replies,
        "expected": expected,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("workspace", type=Path)
    args = parser.parse_args()
    args.workspace.mkdir(parents=True, exist_ok=False)
    make_fixtures(args.workspace)
    service = WorkflowService(
        args.workspace, authorized_external_paths=[args.archive.resolve()]
    )
    draft = service.install_workflow(archive=args.archive.resolve())
    for manual in (False, True):
        built = service.install_workflow(
            draft_id=draft["draft_id"],
            resolution=resolution(draft, manual),
            cases=[case(manual)],
        )
        print(json.dumps(built, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
