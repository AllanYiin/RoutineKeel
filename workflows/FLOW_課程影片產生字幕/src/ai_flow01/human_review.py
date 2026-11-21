"""Offline, human-facing review exports. No model calls or web service."""
from __future__ import annotations
import base64
import csv
import hashlib
import html
import io
import json
import re
import zipfile
import wave
from collections import defaultdict
from pathlib import Path

EMBED_AUDIO_LIMIT = 24*1024*1024
HEADERS = ['片段', '開始', '結束', 'ASR原文', '目前校訂', '疑義', '確認文字', '資料版本']
REASONS = {
 'approximate_split_timing':'分句時間為估算，請核對字幕同步',
 'no_internal_speech_pause':'片段超限，音軌內找不到兩側都有語音的明顯停頓，已保留原段，請聽審',
 'pause_audio_unavailable':'缺少可核對停頓的音軌，已保留原段，請聽審',
 'pause_text_indivisible':'音軌有停頓，但文字沒有可分割的位置，請聽審',
 'pause_text_estimated':'已在實測停頓處分割，字詞時間不足，請核對兩側字幕文字',
 'low_asr_confidence':'辨識信心偏低，請聽原音',
 'possible_silence_hallucination':'可能把靜音誤辨成文字，請聽原音',
 'spot_check_unflagged':'抽樣聽審（未偵測到其他疑義）',
 'qa_invalid_time':'字幕時間無效', 'invalid_time':'字幕時間無效',
 'qa_line_limit':'字幕行數或每行字數超限', 'line_limit':'字幕行數或每行字數超限',
 'invalid_word_timeline':'字級時間異常，已使用段落時間；請確認文字與時間',
 'invalid_timing':'段落時間無效', 'proportional_timing':'文字以比例分配時間，請核對同步',
 'low_logprob':'辨識信心偏低，請聽原音', 'spot_check':'抽樣聽審',
 'qa_reading_speed':'字幕閱讀速度偏快', 'qa_short_duration':'字幕停留時間偏短',
 'qa_long_duration':'字幕停留時間過長', 'qa_overlap':'字幕時間重疊',
 'qa_line_width':'字幕每行字數超限', 'qa_line_count':'字幕行數超限',
 'qa_out_of_range':'字幕時間超出影片範圍', 'review_audio_missing':'部分疑義缺少原音片段',
 'reading_speed':'字幕閱讀速度超限', 'short_duration':'字幕停留時間偏短',
 'long_duration':'字幕停留時間過長', 'overlap':'字幕時間重疊',
 'line_width':'字幕每行字數超限', 'line_count':'字幕行數超限',
 'out_of_range':'字幕時間超出影片範圍',
}


def clock(value):
    ms = round(float(value)*1000)
    h, ms = divmod(ms,3600000);m,ms=divmod(ms,60000);s,ms=divmod(ms,1000)
    return f'{h:02}:{m:02}:{s:02}.{ms:03}'


def safe_cell(value):
    value = str(value)
    return "'"+value if value.lstrip().startswith(('=','+','-','@','\t','\r')) else value


def csv_text(rows):
    stream=io.StringIO(newline='');writer=csv.writer(stream)
    writer.writerow(HEADERS)
    writer.writerows([[safe_cell(v) for v in row] for row in rows])
    return '\ufeff'+stream.getvalue()


def names(out):
    manifest=json.loads((out/'manifest.json').read_text(encoding='utf-8-sig'))
    title=manifest.get('source_title') or '課程影片'
    title=re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_',title).strip(' .')[:40] or '課程影片'
    return title, {'page':'review.html','table':title+'.字幕疑義確認表.csv',
                   'transcript':title+'.校對稿.txt','archive':title+'.字幕與確認表.zip'}


def review_revision(out):
    manifest=json.loads((out/'manifest.json').read_text(encoding='utf-8-sig'))
    return hashlib.sha256((out/'review.csv').read_bytes()+b'\0'+
        (out/'transcript.json').read_bytes()+b'\0'+str(manifest.get('source_sha256','')).encode()).hexdigest()


def render(out, rows, issues, audio_paths, qa):
    title, files=names(out)
    raw=json.loads((out/'asr_details.json').read_text(encoding='utf-8-sig')) if (out/'asr_details.json').exists() else {}
    original=raw.get('recognizer_text',{})
    source_rows={str(r['id']):r['text'] for r in json.loads((out/'transcript.json').read_text(encoding='utf-8-sig'))}
    by_id={str(r['id']):r for r in rows}
    grouped=defaultdict(list)
    for issue in issues:grouped[str(issue['id'])].append(issue)
    answers={}
    with (out/'review.csv').open(encoding='utf-8-sig',newline='') as f:
        for r in csv.DictReader(f):
            if r.get('confirmed_text'):answers[r['id']]=r['confirmed_text']
    if (out/'edits.json').exists():
        for sid,text in json.loads((out/'edits.json').read_text(encoding='utf-8-sig')).items():
            if sid in by_id:answers.setdefault(sid,text)
    revision=review_revision(out)
    table=[]
    for sid, findings in grouped.items():
        row=by_id[sid]
        reasons='；'.join(dict.fromkeys(REASONS.get(x['reason'],'請人工確認（'+x['reason']+'）') for x in findings))
        table.append([sid,clock(row['start']),clock(row['end']),original.get(sid,source_rows[sid]),row['text'],reasons,answers.get(sid,''),revision])
    (out/files['table']).write_text(csv_text(table),encoding='utf-8',newline='')
    (out/files['transcript']).write_text('校對稿（'+qa['decision']+'；尚未通過時不可視為定稿）\n\n'+
        '\n'.join(f"[{clock(r['start'])} → {clock(r['end'])}] {r['text']}" for r in rows)+'\n',encoding='utf-8')
    total_audio=sum((out/p).stat().st_size for p in set(audio_paths.values()))
    embedded=total_audio<=EMBED_AUDIO_LIMIT
    players={}
    for sid,path in audio_paths.items():
        players[str(sid)]='data:audio/wav;base64,'+base64.b64encode((out/path).read_bytes()).decode() if embedded else path
    status={'FAIL':'有品質問題，請先修正','REVIEW_REQUIRED':'等待真人聽審','PASS':'已通過真人核准'}[qa['decision']]
    hard=[f for f in qa['findings'] if f.get('severity')!='review']
    hard_html=''.join('<li>'+html.escape(('字幕 '+str(f['cue'])+'：' if 'cue' in f else '')+REASONS.get(f['reason'],'請檢查：'+f['reason']))+'</li>' for f in hard)
    html_rows=[]
    audio_metadata = json.loads((out/'review_audio.json').read_text(encoding='utf-8-sig')) if (out/'review_audio.json').is_file() else {}
    exported_cues = json.loads((out/'cues.json').read_text(encoding='utf-8-sig')) if (out/'cues.json').is_file() else []
    for row in table:
        sid=row[0]
        cells=''.join('<td>'+html.escape(str(v))+'</td>' for v in row[:6])
        audio=players.get(sid)
        player='<audio controls preload="none" src="'+html.escape(audio,quote=True)+'"></audio>' if audio else '<strong>原音缺失，請回到原影片確認</strong>'
        window=audio_metadata.get('clips',{}).get(sid)
        if audio and window:
            rate=audio_metadata['sample_rate']
            path=out/audio_paths.get(int(sid),audio_paths.get(sid))
            with wave.open(str(path),'rb') as clip:
                valid=(clip.getframerate()==rate and clip.getnframes()==window['frames'])
            source_row=by_id[sid]
            valid=valid and (
                window['end_sample']-window['start_sample']==window['frames']
                and window['segment_start_sample']==round(source_row['start']*rate)
                and window['segment_end_sample']==round(source_row['end']*rate)
                and 0 <= window['start_sample'] <= window['segment_start_sample']
                < window['segment_end_sample'] <= window['end_sample'] <= audio_metadata['source_frames'])
            if not valid or hashlib.sha256(path.read_bytes()).hexdigest()!=window['sha256']:
                raise ValueError('REVIEW_AUDIO_TIMELINE: review audio metadata does not match PCM')
            start=window['start_sample']/rate;end=window['end_sample']/rate
            offset_start=(window['segment_start_sample']-window['start_sample'])/rate
            offset_end=(window['segment_end_sample']-window['start_sample'])/rate
            note=(f'原音範圍（原片）：{clock(start)}–{clock(end)}；音檔 {end-start:.3f} 秒。'
                  f'來源段落在播放器 {offset_start:.3f}–{offset_end:.3f} 秒；其餘為聽審前後文。')
            player+='<p class="muted">'+html.escape(note)+'</p>'
        elif audio:
            player+='<p class="muted">此音檔可能含前後文；舊版未記錄裁切範圍，請重建確認頁核對。</p>'
        matching=[(index,c) for index,c in enumerate(exported_cues,1) if str(c.get('source_id'))==sid]
        if matching:
            player+='<div class="muted">對應 SRT：'+'；'.join(
                html.escape(f"#{index} {clock(c['start'])}–{clock(c['end'])}（{c['end']-c['start']:.3f} 秒）")
                for index,c in matching)+'</div>'
        html_rows.append('<tr data-id="'+html.escape(sid,quote=True)+'">'+cells+'<td>'+player+'</td><td><textarea aria-label="片段 '+html.escape(sid,quote=True)+' 確認文字">'+html.escape(row[6])+'</textarea></td></tr>')
    data=json.dumps({'rows':table,'headers':HEADERS,'filename':title+'.字幕疑義填答.csv','revision':revision},ensure_ascii=False).replace('<','\\u003c').replace('&','\\u0026')
    template=Path(__file__).with_name('review_page.html').read_text(encoding='utf-8')
    replacements={'TITLE':html.escape(title),'STATUS':status,'CUES':str(qa['cue_count']),
        'COUNT':str(len(table)),'ISSUES':str(qa['issue_count']),'HARD':str(len(hard)),
        'AUDIO':str(len(audio_paths)),'HARD_LIST':hard_html or '<li>沒有格式硬性問題，仍須完成真人聽審。</li>',
        'ROWS':''.join(html_rows),'DATA':data,
        'AUDIO_NOTE':'原音已內嵌，可單獨分享此頁。' if embedded else '原音較多，請解壓整份 ZIP 並保留 review_clips 資料夾。',
        'TRANSCRIPT':html.escape(files['transcript'],quote=True),'TABLE':html.escape(files['table'],quote=True),
        'ARCHIVE':html.escape(files['archive'],quote=True)}
    template=re.sub(r'@@([A-Z_]+)@@',lambda m: replacements[m.group(1)],template)
    (out/'review.html').write_text(template,encoding='utf-8')
    (out/'review_delivery.json').write_text(json.dumps({**files,'title':title,'review_segments':len(table),
        'review_issues':qa['issue_count'],'embedded_audio':embedded,'revision':revision},ensure_ascii=False,indent=2),encoding='utf-8')
    (out/'README_請先閱讀.txt').write_text(
        f'{title}\n狀態：{status}\n\n1. 雙擊 review.html，查看品質問題並聽取原音。\n'
        '2. 在「確認文字」填入正確原話；空白表示尚未填答。確認原文無誤時，可填入相同文字。\n'
        '3. 點選「下載填答 CSV」。瀏覽器暫存不是正式交付，請下載保存。\n'
        '4. 將 CSV 交給本流程的 review-import 能力匯入並重建字幕；Agent 不需閱讀整份逐字稿。\n'
        '5. 重新檢查結果；解決格式硬性問題並由真人聽審後，才使用 review-approve 核准。\n'
        '文字修正不一定能解決時間問題；仍有硬性問題時需人工調整時間。\n'
        'QA.json 是機器檢查資料，不是主要操作入口。請解壓 ZIP 後再開啟頁面與其他文件。\n',encoding='utf-8')
    return files


def package(out):
    """Refresh shareable ZIP after SRT/VTT and approval-dependent page are ready."""
    if not (out/'review_delivery.json').exists():return
    data=json.loads((out/'review_delivery.json').read_text())
    members=['review.html',data['table'],data['transcript'],'subtitles.srt','subtitles.vtt','QA.json','README_請先閱讀.txt']
    if (out/'review_audio.json').is_file():members.append('review_audio.json')
    if (out/'pause_splits.json').is_file(): members.append('pause_splits.json')
    if not data['embedded_audio']:members += [p.relative_to(out).as_posix() for p in sorted((out/'review_clips').glob('*.wav'))]
    target=out/data['archive'];temp=target.with_suffix('.zip.tmp')
    with zipfile.ZipFile(temp,'w',zipfile.ZIP_DEFLATED) as z:
        for name in members:z.write(out/name,name)
    temp.replace(target)


def import_answers(out, csv_path):
    """Validate provenance and every row before staging any human edits."""
    delivery=json.loads((out/'review_delivery.json').read_text())
    if delivery['revision'] != review_revision(out):
        raise ValueError('工作資料已變動，請先重建確認頁再填答')
    with (out/delivery['table']).open(encoding='utf-8-sig',newline='') as f:
        expected={r['片段']:r for r in csv.DictReader(f)}
    if Path(csv_path).stat().st_size>10*1024*1024:raise ValueError('填答 CSV 過大')
    edits_path=out/'edits.json'
    edits=json.loads(edits_path.read_text(encoding='utf-8-sig')) if edits_path.exists() else {}
    if not isinstance(edits,dict):raise ValueError('既有人工修訂格式不正確')
    additions={};seen=set()
    with Path(csv_path).open(encoding='utf-8-sig',newline='') as f:
        reader=csv.DictReader(f)
        if reader.fieldnames!=HEADERS:raise ValueError('請使用本版確認頁下載的填答 CSV；欄位不符')
        for row in reader:
            sid=row['片段']
            if sid in seen or sid not in expected:raise ValueError('重複或未知片段：'+str(sid))
            seen.add(sid)
            if any(row[k]!=expected[sid][k] for k in HEADERS if k!='確認文字'):
                raise ValueError('填答版本或原文不符，請使用目前確認頁：'+sid)
            text=row['確認文字']
            if text is None:raise ValueError('填答 CSV 欄位不完整')
            if not text.strip():continue
            if len(text)>5000:raise ValueError('單段填答過長：'+sid)
            # Undo only the Excel formula guard used by our exporter.
            if text.startswith("'") and safe_cell(text[1:])==text:text=text[1:]
            additions[sid]=text
    if not additions:raise ValueError('沒有已填寫的確認文字；未修改字幕')
    updated={**edits,**additions}
    def write_json(path, value):
        temp=path.with_name(path.name+'.tmp')
        temp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        temp.replace(path)
    if edits_path.exists():
        old=edits_path.read_bytes();folder=out/'versions';folder.mkdir(exist_ok=True)
        (folder/(hashlib.sha256(old).hexdigest()+'.edits.json')).write_bytes(old)
    write_json(edits_path,updated)
    # The old approval must not silently bless new wording. Keep its evidence.
    approval=out/'approval.json'
    if approval.exists():
        old=approval.read_bytes();folder=out/'versions';folder.mkdir(exist_ok=True)
        approval.replace(folder/(hashlib.sha256(old).hexdigest()+'.approval.json'))
    return len(additions)
