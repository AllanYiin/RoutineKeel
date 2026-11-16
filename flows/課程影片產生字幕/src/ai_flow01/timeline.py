"""Conservative word-alignment fallback; segment timing is never invented."""
from copy import deepcopy
from pydantic import ValidationError
from .contracts import Segment
from flow_base.runtime import FlowError


def normalize_word_timelines(rows, duration):
    from .subtitles import validate_segments
    normalized, issues = [], []
    for row in rows:
        # Invalid segment bounds, IDs or text are fatal, even if word timing can be dropped.
        try:
            bare = Segment.model_validate({**row, 'words': []}).model_dump()
        except ValidationError as exc:
            raise FlowError('TRANSCRIPT_TIMELINE', '段落本身時間或結構無效，不能自動修復',
                            segment_id=row.get('id')) from exc
        try:
            valid = Segment.model_validate(row).model_dump()
            last = valid['start']
            for word in valid['words']:
                # No zero-length words, overlaps, out-of-segment bounds or time reversal.
                if word['start'] < last or word['end'] <= word['start'] or word['end'] > valid['end']:
                    raise ValueError('nonpositive_overlapping_or_out_of_bounds')
                last = word['end']
        except (ValidationError, ValueError):
            valid = bare
            issues.append({'id': bare['id'], 'start': bare['start'], 'end': bare['end'],
                           'reason': 'invalid_word_timeline', 'handling': 'segment_timing_only',
                           'raw_words': deepcopy(row.get('words', []))})
        normalized.append(valid)
    return validate_segments(normalized, duration), issues
