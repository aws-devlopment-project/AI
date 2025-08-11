import boto3
import json
from typing import Dict, List
from settings import settings

class BedrockService:
    """AWS Bedrock AI 모델 호출 서비스"""
    
    def __init__(self):
        # us-east-1에서 권한이 있다고 하니 시도
        bedrock_region = "us-east-1"
        
        self.client = boto3.client(
            'bedrock-runtime',
            region_name=bedrock_region,
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key
        )
        
        # 정확한 모델 ID들로 시도 (Inference Profile 포함)
        self.model_candidates = [
            # Inference Profile ARN 형태
            "us.anthropic.claude-3-5-sonnet-20241022-v2:0",
            "us.anthropic.claude-3-5-sonnet-20240620-v1:0", 
            # 일반 모델 ID
            "anthropic.claude-3-5-sonnet-20241022-v2:0",
            "anthropic.claude-3-5-sonnet-20240620-v1:0",
            "anthropic.claude-3-sonnet-20240229-v1:0",
            "anthropic.claude-3-haiku-20240307-v1:0"
        ]
        
        self.model_id = None
        print(f"🤖 Bedrock 리전: {bedrock_region}")
        
        # 사용 가능한 모델 찾기
        self._find_working_model()

    def _find_working_model(self):
        """실제로 작동하는 모델 찾기"""
        for model_id in self.model_candidates:
            try:
                print(f"🔍 모델 테스트 중: {model_id}")
                test_response = self._invoke_claude(model_id, "Hello")
                self.model_id = model_id
                print(f"✅ 작동하는 모델 발견: {model_id}")
                return
            except Exception as e:
                error_msg = str(e)
                if "ValidationException" in error_msg:
                    print(f"❌ {model_id}: ValidationException - 모델 접근 불가")
                elif "throttling" in error_msg.lower():
                    print(f"⏳ {model_id}: 요청 제한 - 잠시 후 재시도")
                else:
                    print(f"❌ {model_id}: {error_msg[:100]}...")
                continue
        
        print("❌ 모든 모델 접근 실패")
        self.model_id = None

    def generate_quests(self, document_content: str, user_request: str) -> str:
        """문서 내용과 사용자 요청을 바탕으로 퀘스트 생성"""
        
        if not self.model_id:
            return self._create_fallback_response(user_request)
            
        prompt = f"""당신은 개인화된 습관 형성 퀘스트를 만드는 전문가입니다.

다음 문서 내용을 참고하여 사용자 요청에 맞는 IF-THEN 형태의 퀘스트 3개를 생성해주세요.

=== 참고 문서 ===
{document_content[:1500]}

=== 사용자 요청 ===
{user_request}

=== 요구사항 ===
1. 각 퀘스트는 IF-THEN 형태로 작성
2. IF는 일상에서 자주 하는 행동 (양치, 식사, 출퇴근 등)
3. THEN은 구체적이고 실행 가능한 행동
4. 난이도는 쉬운 것부터 시작
5. 실행 시간은 5분 이내

=== 출력 형식 ===
다음 JSON 형식으로만 응답해주세요:

{{
  "quests": [
    {{
      "title": "퀘스트 제목",
      "if_condition": "IF 조건",
      "then_action": "THEN 행동",
      "estimated_time": "예상 시간",
      "difficulty": "쉬움/보통/어려움"
    }}
  ]
}}"""

        try:
            response = self._invoke_claude(self.model_id, prompt)
            return response
            
        except Exception as e:
            print(f"❌ 퀘스트 생성 실패: {e}")
            return self._create_fallback_response(user_request)

    def _invoke_claude(self, model_id: str, prompt: str) -> str:
        """Claude 모델 호출"""
        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 1000,
            "messages": [
                {
                    "role": "user",
                    "content": prompt
                }
            ]
        }
        
        response = self.client.invoke_model(
            modelId=model_id,
            contentType='application/json',
            accept='application/json',
            body=json.dumps(body)
        )
        
        result = json.loads(response['body'].read().decode())
        return result['content'][0]['text']

    def _create_fallback_response(self, user_request: str) -> str:
        """AI 호출 실패시 대체 응답"""
        
        # 운동 관련 키워드 체크
        exercise_keywords = ['운동', '헬스', '트레이닝', '피트니스', '체력', '근력']
        is_exercise = any(keyword in user_request for keyword in exercise_keywords)
        
        if is_exercise:
            return self._create_exercise_fallback()
        else:
            return self._create_generic_fallback(user_request)

    def _create_exercise_fallback(self) -> str:
        """운동 관련 특화 대체 응답"""
        return """{
  "quests": [
    {
      "title": "아침 스트레칭 퀘스트",
      "if_condition": "아침에 잠에서 깨어난 후",
      "then_action": "침대에서 간단한 스트레칭 3가지 동작하기 (목, 어깨, 허리)",
      "estimated_time": "3분",
      "difficulty": "쉬움"
    },
    {
      "title": "점심 후 산책 퀘스트", 
      "if_condition": "점심 식사를 마친 후",
      "then_action": "건물 주변을 5분간 천천히 걷기",
      "estimated_time": "5분",
      "difficulty": "쉬움"
    },
    {
      "title": "취침 전 운동 계획 퀘스트",
      "if_condition": "잠자리에 들기 전",
      "then_action": "내일 할 운동을 구체적으로 계획하고 운동복 준비하기",
      "estimated_time": "2분", 
      "difficulty": "쉬움"
    }
  ]
}"""

    def _create_generic_fallback(self, user_request: str) -> str:
        """일반적인 대체 응답"""
        return f"""{{
  "quests": [
    {{
      "title": "기본 퀘스트 1",
      "if_condition": "아침에 양치를 마친 후",
      "then_action": "{user_request}와 관련된 간단한 행동 1분 실행",
      "estimated_time": "1분",
      "difficulty": "쉬움"
    }},
    {{
      "title": "기본 퀘스트 2", 
      "if_condition": "점심 식사를 마친 후",
      "then_action": "{user_request}에 대해 5분간 생각해보기",
      "estimated_time": "5분",
      "difficulty": "보통"
    }},
    {{
      "title": "기본 퀘스트 3",
      "if_condition": "잠자리에 들기 전",
      "then_action": "오늘의 {user_request} 관련 행동 돌아보기",
      "estimated_time": "3분", 
      "difficulty": "쉬움"
    }}
  ]
}}"""

    def test_connection(self) -> bool:
        """Bedrock 연결 테스트"""
        if not self.model_id:
            print("❌ 사용 가능한 모델이 없습니다")
            return False
            
        try:
            test_prompt = "Hello"
            response = self._invoke_claude(self.model_id, test_prompt)
            print(f"✅ Bedrock 연결 성공! 사용 모델: {self.model_id}")
            return True
            
        except Exception as e:
            print(f"❌ Bedrock 연결 실패: {e}")
            return False

# 전역 서비스 인스턴스
bedrock_service = BedrockService()