from dataclasses import dataclass


@dataclass(frozen=True)
class SpeakerMatchResult:
    speaker_name: str
    best_name: str
    best_score: float
    second_best_score: float
    reason: str


def select_speaker_match(scores: dict[str, float], threshold: float, min_margin: float) -> SpeakerMatchResult:
    if not scores:
        return SpeakerMatchResult(
            speaker_name='Unknown',
            best_name='Unknown',
            best_score=-1.0,
            second_best_score=-1.0,
            reason='no_candidates',
        )

    best_name = 'Unknown'
    best_score = -1.0
    second_best_score = -1.0

    for name, score in scores.items():
        if score > best_score:
            second_best_score = best_score
            best_score = score
            best_name = name
        elif score > second_best_score:
            second_best_score = score

    if best_score < threshold:
        return SpeakerMatchResult(
            speaker_name='Unknown',
            best_name=best_name,
            best_score=best_score,
            second_best_score=second_best_score,
            reason='below_threshold',
        )

    if second_best_score > -1.0 and (best_score - second_best_score) < min_margin:
        return SpeakerMatchResult(
            speaker_name='Unknown',
            best_name=best_name,
            best_score=best_score,
            second_best_score=second_best_score,
            reason='margin_too_small',
        )

    return SpeakerMatchResult(
        speaker_name=best_name,
        best_name=best_name,
        best_score=best_score,
        second_best_score=second_best_score,
        reason='matched',
    )
