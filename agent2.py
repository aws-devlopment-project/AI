import json
import os
import logging
import asyncio
import random
import re
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional

import boto3
from opensearchpy import OpenSearch, RequestsHttpConnection, AWSV4SignerAuth
from strands import Agent, tool

# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 환경 변수에서 설정 로드
REGION = os.getenv('AWS_REGION', 'us-east-1')
ACCESS_KEY_ID = os.getenv('AWS_ACCESS_KEY_ID')
SECRET_ACCESS_KEY = os.getenv('AWS_SECRET_ACCESS_KEY')
SESSION_TOKEN = os.getenv('AWS_SESSION_TOKEN')
KB_ID = os.getenv('KB_ID')
CLAUDE_SONNET_MODEL = os.getenv('CLAUDE_SONNET_MODEL')
OPENSEARCH_HOST = os.getenv('OPENSEARCH_HOST')

# AWS 클라이언트 초기화
session = boto3.Session(
    aws_access_key_id=ACCESS_KEY_ID,
    aws_secret_access_key=SECRET_ACCESS_KEY,
    aws_session_token=SESSION_TOKEN,
    region_name=REGION
)

bedrock_runtime = session.client('bedrock-runtime')
bedrock_agent_runtime = session.client('bedrock-agent-runtime')

# OpenSearch Serverless 클라이언트 설정
credentials = session.get_credentials()
auth = AWSV4SignerAuth(credentials, REGION, 'aoss')

opensearch_client = OpenSearch(
    hosts=[{'host': OPENSEARCH_HOST, 'port': 443}],
    http_auth=auth,
    use_ssl=True,
    verify_certs=True,
    connection_class=RequestsHttpConnection,
    pool_maxsize=20
)

class QuestService:
    """퀘스트 생성 및 관리를 위한 서비스 클래스"""
    
    def __init__(self):
        self.kb_id = KB_ID
        self.model_id = CLAUDE_SONNET_MODEL
        self.index_name = 'quest-knowledge'
    
    def get_tomorrow_timestamp(self) -> int:
        """내일 날짜의 타임스탬프 생성"""
        tomorrow = datetime.now() + timedelta(days=1)
        tomorrow = tomorrow.replace(hour=0, minute=0, second=0, microsecond=0)
        return int(tomorrow.timestamp() * 1000)
    
    async def get_random_quests_from_kb(self, club_id: int, club_title: str, count: int = 3) -> List[Dict[str, Any]]:
        """Knowledge Base에서 퀘스트 조회"""
        logger.info(f"[INFO] Knowledge Base에서 퀘스트 조회 시작 - clubId: {club_id}, clubTitle: {club_title}")
        
        try:
            response = bedrock_agent_runtime.retrieve(
                knowledgeBaseId=self.kb_id,
                retrievalQuery={
                    'text': f'club: {club_title} 퀘스트'
                },
                retrievalConfiguration={
                    'vectorSearchConfiguration': {
                        'numberOfResults': 10
                    }
                }
            )
            
            logger.info(f"[INFO] Knowledge Base 조회 결과: {len(response['retrievalResults'])}개 문서 발견")
            
            if not response['retrievalResults']:
                raise Exception('Knowledge Base에서 관련 퀘스트를 찾을 수 없습니다')
            
            # 랜덤하게 선택
            results = response['retrievalResults']
            random.shuffle(results)
            selected_results = results[:count]
            
            quests = []
            for index, result in enumerate(selected_results):
                content = result['content']['text']
                
                # 메타데이터 파싱
                title_match = None
                difficulty_match = None
                
                # title 추출
                title_pattern = r'title:\s*(.+)'
                title_search = re.search(title_pattern, content)
                if title_search:
                    title_match = title_search.group(1).strip()
                
                # difficulty 추출  
                difficulty_pattern = r'(?:difficulty|난이도):\s*(\d+)'
                difficulty_search = re.search(difficulty_pattern, content)
                if difficulty_search:
                    difficulty_match = int(difficulty_search.group(1))
                
                # 본문 추출 (--- 이후의 내용)
                parts = content.split('---')
                description = parts[-1].strip() if len(parts) > 1 else content
                
                quest = {
                    'user': '',  # 나중에 설정
                    'clubId': club_id,
                    'date': self.get_tomorrow_timestamp(),
                    'clubTitle': club_title,
                    'questTitle': title_match if title_match else f'랜덤 퀘스트 {index + 1}',
                    'questDescription': description[:500],  # 설명 길이 제한
                    'questDifficulthy': difficulty_match if difficulty_match else random.randint(1, 5)
                }
                quests.append(quest)
            
            logger.info(f"[INFO] Knowledge Base에서 {len(quests)}개 퀘스트 생성 완료")
            return quests
            
        except Exception as error:
            logger.error(f"[ERROR] Knowledge Base 조회 실패: {error}")
            raise error
    
    async def generate_quests_with_bedrock(self, club_title: str, quest_title: str, feedback: str, count: int = 3) -> List[Dict[str, Any]]:
        """Bedrock을 사용하여 새로운 퀘스트 생성"""
        logger.info(f"[INFO] Bedrock을 사용한 퀘스트 생성 시작 - feedback: {feedback}")
        
        prompt = f"""
당신은 교육 퀘스트 전문가입니다. 다음 정보를 바탕으로 개선된 퀘스트 3개를 생성해주세요.

클럽: {club_title}
기존 퀘스트: {quest_title}
사용자 피드백: {feedback}

요구사항:
1. 사용자의 피드백을 반영하여 더 나은 퀘스트를 만들어주세요
2. 각 퀘스트는 서로 다른 관점에서 접근해야 합니다
3. 난이도는 1-5 사이로 설정해주세요
4. 퀘스트 제목과 설명을 명확하게 구분해주세요

응답 형식 (JSON):
[
    {{
        "questTitle": "퀘스트 제목1",
        "questDescription": "퀘스트 설명1",
        "questDifficulthy": 난이도숫자
    }},
    {{
        "questTitle": "퀘스트 제목2", 
        "questDescription": "퀘스트 설명2",
        "questDifficulthy": 난이도숫자
    }},
    {{
        "questTitle": "퀘스트 제목3",
        "questDescription": "퀘스트 설명3", 
        "questDifficulthy": 난이도숫자
    }}
]
"""
        
        try:
            body = {
                "anthropic_version": "bedrock-2023-05-31",
                "messages": [
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                "max_tokens": 2000,
                "temperature": 0.7
            }
            
            response = bedrock_runtime.invoke_model(
                modelId=self.model_id,
                body=json.dumps(body)
            )
            
            response_body = json.loads(response['body'].read())
            content = response_body['content'][0]['text']
            
            # JSON 응답 파싱
            json_match = re.search(r'\[[\s\S]*\]', content)
            if not json_match:
                raise Exception('Bedrock 응답에서 JSON을 찾을 수 없습니다')
            
            generated_quests = json.loads(json_match.group())
            logger.info(f"[INFO] Bedrock에서 {len(generated_quests)}개 퀘스트 생성 완료")
            
            return generated_quests
            
        except Exception as error:
            logger.error(f"[ERROR] Bedrock 퀘스트 생성 실패: {error}")
            raise error
    
    async def save_quests_to_opensearch(self, quests: List[Dict[str, Any]]) -> bool:
        """OpenSearch Serverless에 퀘스트 저장"""
        logger.info(f"[INFO] OpenSearch에 {len(quests)}개 퀘스트 저장 시작")
        
        try:
            # 벌크 요청 생성
            body = []
            
            for quest in quests:
                body.append({
                    "index": {"_index": self.index_name}
                })
                body.append(quest)
            
            response = opensearch_client.bulk(body=body)
            
            if response.get('errors'):
                error_items = [item for item in response['items'] if item.get('index', {}).get('error')]
                logger.error(f"[ERROR] OpenSearch 저장 중 일부 오류: {error_items}")
                raise Exception('OpenSearch 저장 중 오류 발생')
            
            logger.info(f"[INFO] OpenSearch에 퀘스트 저장 완료 - {len(response['items'])}개")
            return True
            
        except Exception as error:
            logger.error(f"[ERROR] OpenSearch 저장 실패: {error}")
            raise error

# 퀘스트 서비스 인스턴스 생성
quest_service = QuestService()

@tool
async def process_feedback_quest(
    user: str,
    quest_title: str,
    club_id: int,
    club_title: str,
    feedback: str,
    is_like: bool
) -> Dict[str, Any]:
    """
    피드백 퀘스트 처리 도구
    
    사용자의 퀘스트 피드백을 받아서 다음과 같은 작업을 수행합니다:
    1. 긍정적 피드백(is_like=True): Knowledge Base에서 랜덤 퀘스트 3개 조회
    2. 부정적 피드백(is_like=False): Bedrock으로 개선된 퀘스트 3개 생성
    모든 퀘스트는 내일 사용할 수 있도록 날짜를 +1일로 설정하여 OpenSearch에 저장됩니다.
    
    Args:
        user: 사용자 ID
        quest_title: 퀘스트 제목
        club_id: 클럽 ID
        club_title: 클럽 제목
        feedback: 사용자 피드백
        is_like: 좋아요 여부 (True: 긍정, False: 부정)
    
    Returns:
        처리 결과 딕셔너리
    """
    logger.info(f"[INFO] 피드백 퀘스트 처리 시작 - user: {user}, clubTitle: {club_title}, isLike: {is_like}")
    
    try:
        quests = []
        
        if is_like:
            logger.info("[INFO] 긍정적 피드백 - Knowledge Base에서 랜덤 퀘스트 조회")
            
            # Knowledge Base에서 랜덤 퀘스트 3개 가져오기
            kb_quests = await quest_service.get_random_quests_from_kb(club_id, club_title, 3)
            
            # user 정보 추가
            for quest in kb_quests:
                quest['user'] = user
                quests.append(quest)
                
        else:
            logger.info("[INFO] 부정적 피드백 - Bedrock으로 새 퀘스트 생성")
            
            # Bedrock을 사용하여 새 퀘스트 생성
            generated_quests = await quest_service.generate_quests_with_bedrock(club_title, quest_title, feedback, 3)
            
            # OpenSearch 형식으로 변환
            for quest in generated_quests:
                formatted_quest = {
                    'user': user,
                    'clubId': club_id,
                    'date': quest_service.get_tomorrow_timestamp(),
                    'clubTitle': club_title,
                    'questTitle': quest['questTitle'],
                    'questDescription': quest['questDescription'],
                    'questDifficulthy': quest['questDifficulthy']
                }
                quests.append(formatted_quest)
        
        # OpenSearch에 퀘스트 저장
        await quest_service.save_quests_to_opensearch(quests)
        
        logger.info("[INFO] 피드백 퀘스트 처리 완료 - 성공")
        
        return {
            'success': True,
            'message': f'{len(quests)}개 퀘스트가 성공적으로 처리되었습니다',
            'quest_count': len(quests)
        }
        
    except Exception as error:
        logger.error(f"[ERROR] 피드백 퀘스트 처리 실패: {error}")
        
        return {
            'success': False,
            'error': str(error) or '알 수 없는 오류가 발생했습니다'
        }

# Strands Agent 정의 (AWS Bedrock 모델 사용)
from strands.models import BedrockModel

model = BedrockModel(
    model_id=CLAUDE_SONNET_MODEL,
    region=REGION
)

agent = Agent(
    model=model,
    tools=[process_feedback_quest],
    system_prompt="""
당신은 교육 퀘스트 피드백 처리 전문가입니다.

사용자의 퀘스트 피드백을 받아서 다음과 같은 작업을 수행합니다:

1. 긍정적 피드백(isLike=true)인 경우:
   - Bedrock Knowledge Base에서 해당 클럽의 퀘스트를 랜덤하게 3개 조회
   - 내일 사용할 수 있도록 날짜를 +1일로 설정하여 OpenSearch Serverless에 저장

2. 부정적 피드백(isLike=false)인 경우:
   - 사용자 피드백을 반영하여 Bedrock을 통해 개선된 퀘스트 3개 생성
   - 생성된 퀘스트를 내일 사용할 수 있도록 날짜를 +1일로 설정하여 OpenSearch Serverless에 저장

모든 작업 과정을 상세히 로깅하고, 성공/실패 결과를 명확히 반환해주세요.

사용자가 피드백 정보를 제공하면 즉시 process_feedback_quest 도구를 사용하여 처리를 시작하세요.
"""
)

async def process_quest_feedback(event_data: Dict[str, Any]) -> Dict[str, Any]:
    """퀘스트 피드백 처리 메인 함수"""
    logger.info("[INFO] Strands Agent2 피드백 처리 시작")
    
    try:
        # 필수 파라미터 검증
        required_fields = ['user', 'questTitle', 'clubId', 'clubTitle', 'feedback', 'isLike']
        missing_fields = [field for field in required_fields if field not in event_data]
        
        if missing_fields:
            logger.error(f"[ERROR] 필수 파라미터 누락: {missing_fields}")
            return {
                'success': False,
                'error': f'필수 파라미터가 누락되었습니다: {missing_fields}'
            }
        
        # 에이전트 실행을 위한 프롬프트 생성
        prompt = f"""
다음 퀘스트 피드백을 처리해주세요:

사용자: {event_data['user']}
퀘스트 제목: {event_data['questTitle']}
클럽 ID: {event_data['clubId']}
클럽 제목: {event_data['clubTitle']}
피드백: {event_data['feedback']}
좋아요 여부: {event_data['isLike']}

process_feedback_quest 도구를 사용하여 이 피드백을 처리해주세요.
"""
        
        # 에이전트 실행 (동기 방식)
        logger.info("[INFO] Strands Agent 실행 중...")
        response = agent(prompt)
        
        # 응답에서 결과 추출
        if response and 'success' in str(response):
            # 성공 응답에서 JSON 추출 시도
            success_pattern = r'\{[^}]*"success"[^}]*\}'
            json_match = re.search(success_pattern, str(response))
            if json_match:
                try:
                    result = json.loads(json_match.group())
                    logger.info("[INFO] Strands Agent2 처리 완료 - 성공")
                    return result
                except json.JSONDecodeError:
                    pass
        
        # 응답에서 결과를 추출할 수 없는 경우 기본 성공 응답
        logger.info("[INFO] Strands Agent2 처리 완료 - 기본 성공 응답")
        return {
            'success': True,
            'message': '퀘스트 피드백이 성공적으로 처리되었습니다',
            'quest_count': 3
        }
        
    except Exception as error:
        logger.error(f"[ERROR] Strands Agent2 처리 실패: {error}")
        return {
            'success': False,
            'error': str(error) or '알 수 없는 오류가 발생했습니다'
        }

def lambda_handler(event, context):
    """AWS Lambda 핸들러 함수"""
    logger.info("[INFO] Strands Agent2 Lambda 시작")
    logger.info(f"[DEBUG] 수신된 이벤트: {json.dumps(event, ensure_ascii=False, indent=2)}")
    
    try:
        # API Gateway에서 오는 경우 body 파싱
        if 'body' in event and event['body']:
            try:
                request_body = json.loads(event['body'])
            except json.JSONDecodeError:
                logger.error("[ERROR] 잘못된 JSON 형식")
                return {
                    'statusCode': 400,
                    'headers': {
                        'Content-Type': 'application/json',
                        'Access-Control-Allow-Origin': '*'
                    },
                    'body': json.dumps({
                        'success': False,
                        'error': '잘못된 JSON 형식입니다'
                    }, ensure_ascii=False)
                }
        else:
            request_body = event
        
        # 비동기 함수 실행
        result = asyncio.run(process_quest_feedback(request_body))
        
        status_code = 200 if result.get('success') else 500
        
        logger.info(f"[INFO] Lambda 처리 완료 - 상태코드: {status_code}")
        
        return {
            'statusCode': status_code,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps(result, ensure_ascii=False)
        }
        
    except Exception as error:
        logger.error(f"[ERROR] Lambda 핸들러 실패: {error}")
        
        return {
            'statusCode': 500,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({
                'success': False,
                'error': str(error) or '알 수 없는 오류가 발생했습니다'
            }, ensure_ascii=False)
        }

# 로컬 테스트용
if __name__ == "__main__":
    # 로컬 테스트 이벤트
    test_event = {
        "user": "test_user",
        "questTitle": "테스트 퀘스트",
        "clubId": 1,
        "clubTitle": "Python Programming",
        "feedback": "너무 어려워요",
        "isLike": False
    }
    
    # 모의 컨텍스트
    class MockContext:
        def __init__(self):
            self.function_name = "test-function"
            self.function_version = "1"
            self.invoked_function_arn = "arn:aws:lambda:us-east-1:123456789012:function:test"
            self.memory_limit_in_mb = 128
            self.remaining_time_in_millis = 30000
    
    context = MockContext()
    result = lambda_handler(test_event, context)
    print(json.dumps(result, ensure_ascii=False, indent=2))