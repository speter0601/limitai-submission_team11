# CutLing

네이버 검색에서 홍보·샛길 글은 접어 두고, 직접 담은 글만으로 학습 리포트를 만들어 주는 검색 집중 도우미.

검색 → 모으기 → 묻기 → 리포트

## 구성

```
app/          FastAPI 서버 (판별 파이프라인, 세션, 묻기, 학습 리포트)
static/       웹 화면
extension/    크롬 확장 프로그램 (네이버 결과 판별, 페이지 담기 패널)
scripts/      API 연결 확인, 토큰 사용량 집계, 판별 벤치마크
```

## 실행

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # CLOVA_API_KEY 입력
uvicorn app.main:app --reload
```

http://localhost:8000

## 크롬 확장 설치

1. `chrome://extensions` → 개발자 모드 켜기
2. 압축해제된 확장 프로그램 로드 → `extension` 폴더 선택

## 사용

1. localhost:8000에서 검색어 입력 → 집중 검색 시작
2. 네이버 결과에서 광고·홍보·샛길·중복 글이 흐리게 접힘
3. 읽다가 좋은 글은 패널의 "이 페이지 담기"
4. "검색 마치고 리포트 받기" → 담은 글에게 묻기 / 학습 리포트 만들기

## 사용 모델 (HyperCLOVA X)

| 단계 | 모델 |
|---|---|
| 규칙 판별 (광고 영역, 협찬 표기) | 없음 |
| 주제 이탈·중복 | bge-m3 |
| 결과 일괄 판정, 로드맵, 요약, 질문 답변 | HCX-DASH-002 |
| 애매한 글 재판정, 학습 리포트 | HCX-007 |

## 도구

```bash
python scripts/check_api.py                 # API 키 확인
python scripts/usage.py                     # 기능별 토큰 사용량
python scripts/bench_focus.py               # 우리 파이프라인 vs 전부 HCX-007 비교
```

검색 기록과 담은 글은 `data/`에만 저장되고, 학습 노트에서 삭제하면 함께 지워집니다.
