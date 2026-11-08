# 추출 데이터

`scripts/export.py`가 `data/<name>/<YYYY-MM-DD>.csv`로 내린다. 스키마는 `docs/data-contract.md`에 있다.

고객 텔레메트리이므로 `.gitignore`로 커밋에서 제외한다. `system_prompts`와 `content_by_span`은 고객 콘텐츠 원문을 담으므로, 진단이 끝나면 지운다.
