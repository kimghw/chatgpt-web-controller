---
description: "git 작업 자동화. 인자 없으면 stage+commit+push, 'pull'이면 pull, 'revert'면 미커밋 변경 전체 취소, 'public|private'이면 원격 저장소 공개여부 변경"
allowed-tools: Bash, Read, Grep, Glob, AskUserQuestion
---

# /git 명령

인자: $ARGUMENTS

## 동작 규칙

0. **인자가 `help` / `-h` / `--help`인 경우**: 본 명령이 받는 인자 목록과 각 인자의 동작 설명을 한눈에 출력하고 종료 (실제 git 작업 수행 안 함).

   출력 형식 (예시):
   ```
   /git [인자]

   (없음)            stage(-A) + 커밋 메시지 자동 생성 + push
   pull              git pull 실행
   revert            현재 미커밋 변경(staged+unstaged+untracked) 전체 취소 — 사용자 확인 필요
   public            현재 레포의 원격 저장소(origin)를 공개(public)로 전환
   private           현재 레포의 원격 저장소(origin)를 비공개(private)로 전환
   help|-h|--help    이 도움말 출력
   <기타>            git 서브커맨드로 그대로 전달 (예: /git status)
   ```

1. **인자가 비어 있거나 없는 경우** (기본 동작):
   - `git add -A`로 모든 변경사항 스테이지
   - `git diff --cached --stat`으로 스테이지된 내용 확인
   - 변경사항이 없으면 "커밋할 내용 없음" 출력 후 종료
   - 변경사항이 있으면 diff를 분석해 간결한 커밋 메시지 자동 생성 (한국어, 1줄)
   - `git commit`으로 커밋
   - `git push`로 현재 브랜치에 푸시 (upstream 없으면 `-u origin <branch>` 사용)

2. **인자가 `pull`인 경우**:
   - `git pull`을 실행하고 결과를 보여줌

3. **인자가 `revert`인 경우** (미커밋 변경 전체 취소 — 파괴적):
   - 현재 상태 미리보기 출력:
     - `git status --short` — 변경 파일 목록
     - `git diff --stat HEAD` — staged+unstaged diff 통계
     - `git ls-files --others --exclude-standard` — untracked 파일 목록
   - 취소 대상이 없으면 (`git status --porcelain` 빈 결과) "취소할 변경 없음" 출력 후 종료.
   - `AskUserQuestion` 으로 사용자 확인:
     > "현재 미커밋 변경을 모두 취소합니다. 복구 불가. 다음 중 선택:"
     - 옵션 1: **취소 (tracked 만)** — `git reset --hard HEAD` 실행. 추적 파일의 staged+unstaged 변경 제거. untracked 파일은 보존.
     - 옵션 2: **취소 + untracked 삭제** — `git reset --hard HEAD && git clean -fd` 실행. 신규 파일·디렉터리까지 모두 제거. (Recommended 아님 — 신규 작업물 손실 위험)
     - 옵션 3: **중단**
   - 옵션 1 또는 2 선택 시 실행, 옵션 3 선택 시 종료.
   - 실행 후 `git status` 한 번 더 출력해 결과 확인.
   - **주의**: `git stash` 와 달리 복구 경로 없음. 사용자가 선택한 옵션 외에는 추가 작업 금지.

4. **인자가 `public` 또는 `private`인 경우** (원격 저장소 공개여부 전환):
   - 사전 조건 확인:
     - `gh --version`으로 GitHub CLI 설치 여부 확인. 없으면 "gh CLI 미설치 — `sudo apt install gh` 또는 https://cli.github.com 설치 후 재시도" 안내 후 종료
     - `gh auth status`로 인증 확인. 미인증이면 "`gh auth login` 먼저 실행" 안내 후 종료
   - 현재 origin 저장소 식별:
     - `git remote get-url origin`으로 원격 URL 획득. 없으면 "origin 미설정" 출력 후 종료
     - URL에서 `OWNER/REPO` 추출 (예: `git@github.com:foo/bar.git` 또는 `https://github.com/foo/bar.git` → `foo/bar`)
   - 현재 가시성 조회: `gh repo view <OWNER/REPO> --json visibility -q .visibility`
     - 이미 요청한 상태와 같으면 "이미 <public|private> 상태입니다" 출력 후 종료
   - 가시성 변경 실행:
     - public 으로 전환: `gh repo edit <OWNER/REPO> --visibility public --accept-visibility-change-consequences`
     - private 으로 전환: `gh repo edit <OWNER/REPO> --visibility private --accept-visibility-change-consequences`
   - 결과 확인: `gh repo view <OWNER/REPO> --json nameWithOwner,visibility,url` 출력으로 전환 후 상태 표시
   - 실패(권한 부족·소유 아님 등) 시 gh 에러 메시지를 그대로 보여주고 종료

5. **그 외 인자**:
   - 인자를 그대로 `git` 명령의 서브커맨드로 전달하여 실행 (예: `/git status` → `git status`)
