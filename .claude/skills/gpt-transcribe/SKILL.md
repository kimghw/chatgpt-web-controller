---
name: gpt-transcribe
description: 오디오 파일(m4a/mp3/wav 등)을 ChatGPT 웹의 Code Interpreter 샌드박스에서 전사(음성→텍스트). 사용자가 "음성/녹음 텍스트로 변환·전사해줘" 하면 이 스킬을 따른다. 검증된 레시피(2026-07-10, 54분 한국어 회의 성공): Pro 모델 + sherpa-onnx + Artifactory 프록시.
---

# gpt-transcribe — ChatGPT 샌드박스에서 오디오 전사

로컬 PC에 STT를 설치하지 않고, ChatGPT 웹의 파이썬 샌드박스가 직접 전사하게 한다.
전제: HTTP 서버(127.0.0.1:8765) 가동 + 로그인 (아니면 /gpt-server 먼저).

## 반드시 지킬 3가지 (실패 경험으로 검증됨)

1. **모델은 `Pro`(GPT-5.6 Sol Pro) 또는 thinking 계열.** `Instant`는 Code Interpreter를 안 쓰고
   "음성 인식 기능을 사용할 수 없다"며 **거부**한다. 관리형 STT(그냥 "전사해줘")도 이 계정에선 안 걸리고,
   걸려도 ~25MB/~25분 캡이 있다.
2. **whisper는 못 쓴다.** 샌드박스는 인터넷 차단 — `pip install`은 내부 프록시로 되지만 whisper
   **가중치 CDN 다운로드는 실패**한다. 대신 **sherpa-onnx 모델을 내부 Artifactory `github-remote`
   프록시로** 받는다 (이건 뚫려 있음, env 인증 사용). 대안: `pip install vosk vosk-model-ko`(가중치 내장, 정확도↓).
3. **샌드박스는 휘발성.** 유휴 수십 분이면 컨테이너가 만료돼 전사본·오디오가 **사라진다**.
   완료되면 **즉시 회수**할 것.

## 절차

1. **전송**: `POST /ask` 로 오디오 첨부 + Pro 모델 + 아래 프롬프트. `wait_timeout: 2400` (Pro는 느림 — 54분 오디오에 약 23분).
   ```json
   {"prompt": "<아래 전사 지시>", "files": ["<오디오 경로>"], "model": "Pro",
    "title": "[전사] <파일명>", "wait_timeout": 2400}
   ```

   **전사 지시 프롬프트**:
   > 업로드한 한국어 녹음을 Code Interpreter에서 직접 전사해줘. 재탐색 없이 아래대로:
   > ① ffmpeg로 mono 16kHz wav 변환 → ② `pip install sherpa-onnx soundfile` 후 한국어 모델을
   > `https://{CAAS_ARTIFACTORY_BASE_URL}/artifactory/github-remote/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2`
   > (auth: env `CAAS_ARTIFACTORY_READER_USERNAME`/`CAAS_ARTIFACTORY_READER_PASSWORD`)로 받아 압축 해제 →
   > ③ ffmpeg `silencedetect`(noise=-30dB:d=0.7)로 무음 기준 27초 이하 청크 분할 →
   > ④ `sherpa_onnx.OfflineRecognizer.from_sense_voice(model=.../model.int8.onnx, tokens=.../tokens.txt,
   > language='ko', use_itn=True, num_threads=4)`로 각 청크 전사, [mm:ss] 타임스탬프와 이어붙이기 →
   > ⑤ `/mnt/data/전사.txt` 저장 + 다운로드 링크 제공. 요약 금지, 들리는 그대로, 불명확은 [불명확]. 설명 최소화.

2. **회수 (완료 즉시!)**: 렌더된 `sandbox:` 다운로드 링크는 href 없는 JS 핸들러라 `expect_download`가
   안 걸릴 수 있다. **확실한 방법**: `POST /ask_in` 으로 이어쓰기 —
   - 먼저 `"…전사.txt 의 총 줄 수와 글자 수만 알려줘"` 로 크기 확인
   - `"N~M번째 줄을 수정 없이 코드블록에 그대로 출력"` 으로 **75줄씩 나눠** 받아 로컬에서 병합
     (한 메시지 출력 한도로 잘림 방지). 코드블록은 ```…``` 정규식으로 추출.
3. **저장**: 병합본을 사용자 지정 위치(기본 `%USERPROFILE%\Downloads\<원본명>_전사.txt`)에 UTF-8 저장,
   줄 수·글자 수가 1단계 확인값과 일치하는지 검증.

## 품질/한계

- sense-voice int8은 속도 우선 — **고유명사·기술용어 오인식** 있음. 정밀도가 중요하면 같은 절차에서
  모델만 `sherpa-onnx-whisper-small`(또는 large, 더 느림)로 교체해 재전사.
- 진행 중 `JS_IS_GEN` 폴링으로 완료 감지 가능. Pro는 "Worked for NNm" 표시가 뜨면 끝난 것.
- 계정에 대화가 생성된다(쓰기 동작). 제목으로 추적하고 필요 없으면 정리.
