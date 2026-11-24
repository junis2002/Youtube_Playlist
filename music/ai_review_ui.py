"""음원 후보와 모델의 권고를 비교하는 읽기 전용 검토 화면."""

import json
import time
from collections.abc import Iterable

import streamlit as st

if __package__:
    from .core import local_model
    from .core.ai_review import Candidate, ReviewCase, baseline_id, build_prompt, parse_reply
else:
    from core import local_model
    from core.ai_review import Candidate, ReviewCase, baseline_id, build_prompt, parse_reply


def show_track_review(artist: str, title: str, album: str, rows: Iterable[tuple[str, str, str, str]],
                      *, context: str) -> None:
    try:
        # 같은 영상이 두 번 나와도 모델과 사용자가 비교할 대상은 한 개다.
        by_id = {}
        for video_id, found_title, found_artist, found_album in rows:
            if video_id not in by_id:
                by_id[video_id] = Candidate(video_id, found_title, found_artist, found_album)
        case = ReviewCase(artist, title, album, tuple(by_id.values()))
    except ValueError as exc:
        st.caption(f"AI 검토를 열 수 없습니다. {exc}")
        return
    show_ai_review(case, context=context)


def show_ai_review(case: ReviewCase, *, context: str) -> None:
    choices = {item.video_id: item for item in case.candidates}

    def display(video_id: str | None) -> str:
        if not video_id:
            return "보류"
        item = choices[video_id]
        return f"{item.title} · {item.artist} · {video_id}"

    key = f"review_{context}_{case.case_id}"
    # 동일 후보·입력의 독립 판정은 탭이나 검토 경로를 바꿔도 한 번만 기록한다.
    evaluation = st.session_state.setdefault("evaluation_" + case.case_id, {})
    with st.expander("AI 후보 검토"):
        st.caption("모델은 현재 후보의 곡명·가수·앨범을 비교합니다. 최종 곡은 아래에서 직접 선택하세요.")
        if "human" in evaluation:
            st.text("평가용 내 판정 · " + display(evaluation["human"]))
        elif not evaluation.get("seen"):
            human_pick = st.selectbox("평가용 내 판정", ["", *choices], index=None, format_func=display,
                                      key=key + "_human", placeholder="선택 사항 · 모델 답을 보기 전에 판정하세요")
            if st.button("내 판정 기록", disabled=human_pick is None, key=key + "_record"):
                evaluation["human"] = human_pick or None
                st.rerun()

        method = st.radio("추천을 받을 곳", ["기존 Codex", "로컬 Ollama"], horizontal=True, key=key + "_method")
        provider = "codex" if method == "기존 Codex" else "ollama"
        result_key = key + "_" + provider + "_result"
        if provider == "codex":
            if st.session_state.get(key + "_prompt"):
                st.caption("코드 상자의 복사 버튼으로 요청을 복사해 사용할 Codex 대화창에 붙여넣으세요. 받은 JSON을 아래에 입력하세요.")
                st.code(build_prompt(case), language="text", wrap_lines=True)
            elif st.button("Codex 요청 보기", key=key + "_reveal",
                           help="평가 파일을 만들려면 요청을 열기 전에 내 판정을 기록하세요."):
                st.session_state[key + "_prompt"] = True
                evaluation["seen"] = True
                st.rerun()
            model = st.text_input("Codex 모델 이름", key=key + "_model", max_chars=100,
                                  help="모델별 평가 파일을 만들 때 사용합니다. 이름을 모르면 비워 두세요.").strip()
            raw = st.text_area("AI 응답 JSON", key=key + "_raw", max_chars=8192)
            if st.button("응답 검증", key=key + "_check", disabled=not raw.strip()):
                st.session_state.pop(result_key, None)
                try:
                    reply = parse_reply(case, raw)
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    evaluation["seen"] = True
                    st.session_state[result_key] = (model, reply, None)
                    st.rerun()
        else:
            if st.button("설치된 모델 확인", key=key + "_refresh"):
                st.session_state.pop(result_key, None)
                st.session_state.pop(key + "_installed", None)
                try:
                    st.session_state[key + "_installed"] = local_model.list_models()
                except local_model.LocalModelError as exc:
                    st.warning(str(exc))
            models = st.session_state.get(key + "_installed")
            if models:
                model = st.selectbox("설치된 로컬 모델", models, key=key + "_ollama")
                if st.button("이 모델로 추천 받기", key=key + "_ask"):
                    st.session_state.pop(result_key, None)
                    try:
                        start = time.monotonic()
                        with st.spinner("이 PC의 모델이 후보를 비교하고 있습니다…"):
                            reply = local_model.recommend(case, model)
                    except local_model.LocalModelError as exc:
                        st.warning(str(exc))
                    else:
                        evaluation["seen"] = True
                        st.session_state[result_key] = (model, reply, time.monotonic() - start)
                        st.rerun()
            elif models == []:
                st.info("이 PC에 설치된 로컬 채팅 모델이 없습니다. 기존 Codex로 검토할 수 있습니다.")

        result = st.session_state.get(result_key)
        if result is None:
            return
        used_model, reply, elapsed = result
        source = "Codex · 직접 입력" if provider == "codex" else "Ollama · 이 PC"
        st.caption("추천 출처 · " + source + (" · " + used_model if used_model else ""))
        st.text("AI 추천 · " + display(reply.video_id))
        st.text(reply.reason)
        baseline = baseline_id(case)
        st.text("기존 유사도 판정 · " + display(baseline))
        if elapsed is not None:
            st.caption(f"로컬 추천 소요 시간 {elapsed:.1f}초")
        if "human" in evaluation and used_model:
            record = {"case_id": case.case_id, "candidate_ids": list(choices), "human": evaluation["human"],
                      "baseline": baseline, "provider": provider, "model": used_model, "ai": reply.video_id}
            st.download_button("평가 사례 JSONL 다운로드", (json.dumps(record, ensure_ascii=False) + "\n").encode(),
                               f"ai-review-{case.case_id[:16]}.jsonl", mime="application/x-ndjson",
                               key=key + "_export_" + provider)
        elif "human" in evaluation:
            st.caption("이 사례를 내보내려면 사용한 모델 이름을 입력하고 응답을 다시 검증하세요.")
