# 2차 자체 평가 결과 (Day 10 · 개선 후)

> 아직 실시 전. 1차 평가(`round1_report.md`)의 실패 분석에 따라 에이전트 지침·스크립트를 개선한 뒤,
> 같은 `test_queries.csv` 20건을 다시 실행하고 아래 절차로 이 파일을 생성한다.

```bash
python evaluation/evaluate.py --round 2 --init     # results_round2.csv 생성
# 20건을 실행하며 results_round2.csv 에 pass(Y/N)·observed·traits_missing·forbidden_hit·tools_observed 기록
python evaluation/evaluate.py --round 2            # → round2_report.md 갱신
```

기록할 것:
- 1차 대비 통과율 변화 (카테고리별)
- 1차 실패 케이스별 개선 조치(어느 파일의 어떤 규칙을 바꿨는지)와 재실행 결과
- 새로 드러난 실패와 남은 과제
- SERVICE.md §5 완성 기준(≥ 85% + guardrail·negative 100%) 충족 여부
