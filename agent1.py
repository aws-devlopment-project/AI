import json
import os
import time
import sys
from datetime import datetime
from typing import List, Dict, Any, Optional

import boto3
from opensearchpy import OpenSearch, RequestsHttpConnection
from strands import Agent, tool
import asyncio
import logging

# OpenSearch AWS 인증 임포트 (버전 호환성 처리)
try:
    from opensearchpy.connection.http_requests import AWSV4SignerAuth
except ImportError:
    try:
        from opensearchpy import AWSV4SignerAuth
    except ImportError:
        # 최신 버전의 opensearch-py 사용
        from opensearchpy.helpers.signer import AWSV4SignerAuth

mapping = {
    "동네탐험대": "daily_walking_data",
    "베개와의 약속": "regular_sleep_schedule_data",
    "물방울 충전소": "regular_investing_data",
    "뼈마디 깨우기": "stretching_data",
    "비타민 사냥꾼": "daily_vegetable_fruits_data",
    "내면 충전기": "meditation_supplements_data",
    "치아 청소부": "oral_hygiene_data",
    "영양 충전소": "sensitive_soothing_care_data",
    "건강 레이더": "preventive_monitoring_data",
    "전자기기 금지구역": "digital_detox_data",
    "월급 도둑": "lifestyle_integrated_care_data",
    "가계부 탐정": "expense_tracking_data",
    "일요일 회계사": "financial_literacy_data",
    "24시간 냉정기": "impulse_spending_control_data",
    "봉투 마법사": "budget_planning_data",
    "빛 퇴치단": "debt_management_data",
    "미래 저금통": "automatic_savings_data",
    "든든한 방패막이": "emergency_fund_data",
    "결제 로봇": "credit_score_management_data",
    "돈 새는 곳 막기단": "fee_subscription_audit_data",
    "햇빛 방패단": "sunscreen_management_data",
    "얼굴 세탁소": "cleansing_routine_optimization_data",
    "수분 충전기": "hydration_data",
    "밤의 재생공장": "moisturizing_routine_data",
    "아침 비타민 폭탄": "seasonal_skincare_strategy_data",
    "피부 리셋 버튼": "exfoilation_skin_texture_data",
    "트러블 119": "acne_blemish_control_data",
    "수염 정원사": "meditation_breathing_data",
    "뷰티 슬립 모드": "whitening_brightening_care_data",
    "속부터 빛나기": "anti_aging_care_data"
}

# ============================================================================
# 로깅 설정 개선 - Lambda 환경에 최적화
# ============================================================================

# Lambda용 로거 설정
def setup_lambda_logger():
    """Lambda 환경용 로거 설정"""
    logger = logging.getLogger()
    
    # 기존 핸들러 제거 (Lambda 기본 핸들러와 충돌 방지)
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
    
    # Lambda 환경에서는 sys.stdout으로 직접 출력
    handler = logging.StreamHandler(sys.stdout)
    
    # 상세한 포맷 설정
    formatter = logging.Formatter(
        '[%(levelname)s] %(asctime)s - %(name)s:%(lineno)d - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    handler.setFormatter(formatter)
    
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    
    return logger

# 로거 초기화
logger = setup_lambda_logger()

# AWS Configuration
AWS_REGION = os.getenv('AWS_REGION', 'us-east-1')
OPENSEARCH_HOST = os.getenv('OPENSEARCH_HOST')
KB_ID = os.getenv('KB_ID')
CLAUDE_SONNET_MODEL = os.getenv('CLAUDE_SONNET_MODEL', 'anthropic.claude-3-5-sonnet-20240620-v1:0')

# Initialize AWS clients
lambda_client = boto3.client('lambda', region_name=AWS_REGION)
bedrock_client = boto3.client('bedrock-runtime', region_name=AWS_REGION)
bedrock_agent_client = boto3.client('bedrock-agent-runtime', region_name=AWS_REGION)

INDEX = 'quest-knowledge'

# ============================================================================
# 로깅 데코레이터 - 함수 실행 추적
# ============================================================================

def log_function_execution(func):
    """함수 실행을 로깅하는 데코레이터"""
    def sync_wrapper(*args, **kwargs):
        func_name = func.__name__
        logger.info(f"🔄 {func_name} 시작")
        try:
            result = func(*args, **kwargs)
            logger.info(f"✅ {func_name} 완료")
            return result
        except Exception as e:
            logger.error(f"❌ {func_name} 실패: {str(e)}")
            raise
    
    async def async_wrapper(*args, **kwargs):
        func_name = func.__name__
        logger.info(f"🔄 {func_name} 시작 (비동기)")
        try:
            result = await func(*args, **kwargs)
            logger.info(f"✅ {func_name} 완료 (비동기)")
            return result
        except Exception as e:
            logger.error(f"❌ {func_name} 실패 (비동기): {str(e)}")
            raise
    
    if asyncio.iscoroutinefunction(func):
        return async_wrapper
    else:
        return sync_wrapper

# ─────────────────────────────────────────────────────────────
# Utilities
# ─────────────────────────────────────────────────────────────

@log_function_execution
def get_today_timestamp() -> int:
    """Get today's timestamp at midnight"""
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    timestamp = int(today.timestamp() * 1000)  # Convert to milliseconds
    logger.info(f"오늘 날짜 타임스탬프: {timestamp}")
    return timestamp

@log_function_execution
def get_opensearch_client():
    """Initialize OpenSearch Serverless client"""
    if not OPENSEARCH_HOST:
        logger.error('OPENSEARCH_HOST 환경변수가 설정되지 않음')
        raise ValueError('OPENSEARCH_HOST 환경변수가 설정되지 않았습니다')
    
    logger.info(f"OpenSearch Serverless 클라이언트 초기화 시작 - Host: {OPENSEARCH_HOST}")
    
    # Import RequestsHttpConnection at the top level to avoid scoping issues
    from opensearchpy import RequestsHttpConnection
    
    # AWS credentials for signing
    session = boto3.Session()
    credentials = session.get_credentials()
    logger.info("AWS 자격증명 획득 완료")
    
    # 버전 호환성을 위한 인증 설정
    auth = None
    try:
        # 최신 opensearch-py 버전 시도
        from opensearchpy import AWSV4SignerAuth
        auth = AWSV4SignerAuth(credentials, AWS_REGION, 'aoss')
        logger.info("AWSV4SignerAuth 사용")
    except ImportError:
        try:
            # 이전 버전 시도
            from opensearchpy.connection.http_requests import AWSV4SignerAuth
            auth = AWSV4SignerAuth(credentials, AWS_REGION, 'aoss')
            logger.info("Legacy AWSV4SignerAuth 사용")
        except ImportError:
            # AWS4Auth 대안 사용 (fallback)
            logger.warning("AWSV4SignerAuth를 찾을 수 없어 AWS4Auth 시도")
            try:
                from requests_aws4auth import AWS4Auth
                auth = AWS4Auth(
                    credentials.access_key,
                    credentials.secret_key,
                    AWS_REGION,
                    'aoss',
                    session_token=credentials.token
                )
                logger.info("AWS4Auth 사용")
            except ImportError:
                logger.error("인증 모듈을 찾을 수 없음")
                raise ImportError("opensearch-py 인증을 위해 'pip install requests-aws4auth' 실행하세요")
    
    if auth is None:
        logger.error("OpenSearch 인증 설정 실패")
        raise ValueError("OpenSearch 인증 설정에 실패했습니다")
    
    client = OpenSearch(
        hosts=[{'host': OPENSEARCH_HOST, 'port': 443}],
        http_auth=auth,
        use_ssl=True,
        verify_certs=True,
        connection_class=RequestsHttpConnection,
        pool_maxsize=20,
    )
    
    logger.info("OpenSearch 클라이언트 생성 완료")
    return client

# ─────────────────────────────────────────────────────────────
# OpenSearch Tools for Strands Agent
# ─────────────────────────────────────────────────────────────

@tool
@log_function_execution
async def ensure_index() -> str:
    """Ensure the quest-knowledge index exists in OpenSearch"""
    logger.info(f"인덱스 존재 확인: {INDEX}")
    
    try:
        client = get_opensearch_client()
        
        exists = client.indices.exists(index=INDEX)
        logger.info(f"인덱스 존재 여부: {exists}")
        
        if exists:
            return f"인덱스 {INDEX}가 이미 존재합니다"
        
        logger.info(f"인덱스가 없어 생성 시작: {INDEX}")
        
        mapping = {
            "settings": {
                "number_of_shards": 1,
                "number_of_replicas": 1
            },
            "mappings": {
                "properties": {
                    "user": {"type": "keyword"},
                    "clubId": {"type": "keyword"},
                    "date": {"type": "long"},
                    "clubTitle": {"type": "keyword"},
                    "questTitle": {"type": "text"},
                    "questDescription": {"type": "text"},
                    "questDifficulthy": {"type": "integer"}  # 오타 필드명 유지
                }
            }
        }
        
        client.indices.create(index=INDEX, body=mapping)
        logger.info(f"인덱스 생성 완료: {INDEX}")
        return f"인덱스 {INDEX} 생성 완료"
        
    except Exception as e:
        logger.error(f"인덱스 생성 실패: {e}", exc_info=True)
        raise e  # 더미 데이터 대신 예외를 다시 발생

@tool
@log_function_execution
async def index_quests(docs: List[Dict[str, Any]]) -> str:
    """Index quest documents in OpenSearch"""
    logger.info(f"퀘스트 색인 시작: {len(docs)}개 문서")
    
    try:
        client = get_opensearch_client()
        
        for i, doc in enumerate(docs):
            # 필수 필드 검증
            required_fields = ['user', 'clubId', 'date', 'questTitle']
            missing_fields = [field for field in required_fields if field not in doc]
            
            if missing_fields:
                logger.error(f"문서 {i+1}에서 필수 필드 누락: {missing_fields}")
                logger.error(f"문서 내용: {doc}")
                continue  # 또는 raise Exception
            
            doc_id = f"{doc['user']}::{doc['clubId']}::{doc['date']}::{doc['questTitle']}"
            logger.info(f"문서 {i+1}/{len(docs)} 색인 중 - ID: {doc_id}")
            
            client.index(
                index=INDEX,
                id=doc_id,
                body=doc
            )
    except Exception as e:
        logger.error(f"퀘스트 색인 실패: {e}", exc_info=True)
        raise e  # 더미 데이터 대신 예외를 다시 발생

@tool
@log_function_execution
async def search_today_quests(user: str, club_id: str) -> Optional[List[Dict[str, Any]]]:
    """Search for today's quests for a user and club"""
    logger.info(f"오늘자 퀘스트 검색 시작 - user: {user}, clubId: {club_id}")
    
    today_timestamp = get_today_timestamp()
    logger.info(f"검색할 타임스탬프: {today_timestamp}")
    
    try:
        client = get_opensearch_client()
        
        # Ensure index exists first
        await ensure_index()
        
        search_query = {
            "query": {
                "bool": {
                    "must": [
                        {"term": {"user": str(user)}},
                        {"term": {"clubId": str(club_id)}},
                        {"term": {"date": today_timestamp}}
                    ]
                }
            },
            "size": 10
        }
        
        logger.info(f"OpenSearch 쿼리 실행: {json.dumps(search_query, indent=2)}")
        
        response = client.search(index=INDEX, body=search_query)
        hits = response.get('hits', {}).get('hits', [])
        
        logger.info(f"OpenSearch 검색 결과: {len(hits)}개 퀘스트 발견")
        
        if hits:
            results = [hit['_source'] for hit in hits]
            logger.info(f"반환할 퀘스트 목록: {[r['questTitle'] for r in results]}")
            return results
        else:
            logger.info("오늘자 퀘스트 없음")
            return None
            
    except Exception as e:
        logger.error(f"퀘스트 검색 실패: {e}", exc_info=True)
        raise e  # None 대신 예외를 다시 발생

# ─────────────────────────────────────────────────────────────
# Knowledge Base Tools
# ─────────────────────────────────────────────────────────────

@tool
@log_function_execution
async def get_random_files_from_kb(club_title: str, count: int = 3) -> List[Dict[str, Any]]:
    """Get random files from Bedrock Knowledge Base"""
    logger.info(f"Knowledge Base 파일 조회 시작 - clubTitle: {club_title}, count: {count}")
    
    if not KB_ID:
        logger.warning("KB_ID가 설정되지 않아 더미 데이터 사용")
        return create_dummy_files(club_title, count)
    try:
        if mapping[club_title]:
            club_title = mapping[club_title]
        logger.info(f"Bedrock Knowledge Base 호출 - KB_ID: {KB_ID}")
        
        response = bedrock_agent_client.retrieve(
            knowledgeBaseId=KB_ID,
            retrievalQuery={'text': f'club: {club_title}'},
            retrievalConfiguration={
                'vectorSearchConfiguration': {
                    'numberOfResults': 10
                }
            }
        )
        
        results = response.get('retrievalResults', [])
        logger.info(f"Knowledge Base 조회 결과: {len(results)}개 문서 발견")
        
        if not results:
            logger.warning("Knowledge Base에서 관련 문서를 찾을 수 없어 더미 데이터 사용")
            return create_dummy_files(club_title, count)
        
        # Random selection
        import random
        random.shuffle(results)
        selected = results[:count]
        
        logger.info(f"✅ {len(selected)}개 파일 랜덤 선택 완료")
        return selected
        
    except Exception as e:
        logger.error(f"Knowledge Base 파일 조회 실패: {e}", exc_info=True)
        logger.warning("Knowledge Base 접근 실패로 더미 데이터 사용")
        return create_dummy_files(club_title, count)

def create_dummy_files(club_title: str, count: int) -> List[Dict[str, Any]]:
    """더미 파일 생성"""
    logger.info(f"더미 파일 생성: {count}개")
    return [
        {'content': {'text': f'title: {club_title} 기초 학습\n\n이것은 {club_title}의 기초를 학습하는 퀘스트입니다.'}},
        {'content': {'text': f'title: {club_title} 중급 실습\n\n이것은 {club_title}의 중급 실습 퀘스트입니다.'}},
        {'content': {'text': f'title: {club_title} 고급 프로젝트\n\n이것은 {club_title}의 고급 프로젝트 퀘스트입니다.'}}
    ][:count]

@tool
@log_function_execution
async def generate_quest_from_markdown(markdown_content: str, club_title: str) -> Dict[str, Any]:
    """Generate a quest from markdown content using Bedrock"""
    logger.info(f"마크다운으로부터 퀘스트 생성 시작 - club: {club_title}")
    logger.info(f"마크다운 내용 길이: {len(markdown_content)} 문자")
    
    if not CLAUDE_SONNET_MODEL:
        logger.warning("CLAUDE_SONNET_MODEL이 설정되지 않아 더미 퀘스트 사용")
        return create_dummy_quest(club_title)
    
    prompt = f"""
당신은 교육 퀘스트 생성 전문가입니다. 아래 마크다운 문서를 바탕으로 퀘스트 1개를 생성해주세요.

클럽: {club_title}
마크다운 내용:
{markdown_content}

요구사항:
1. 마크다운 내용을 기반으로 의미있는 퀘스트를 만들어주세요
2. 퀘스트 제목과 설명을 명확하게 작성해주세요
3. 난이도는 1-5 사이로 설정해주세요 (1: 매우 쉬움, 5: 매우 어려움)
4. 퀘스트 설명은 500자 이내로 작성해주세요

응답 형식 (JSON):
{{
  "questTitle": "퀘스트 제목",
  "questDescription": "퀘스트 설명",
  "questDifficulthy": 난이도숫자
}}
""".strip()
    
    try:
        logger.info("Bedrock Claude 호출 시작")
        
        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": prompt}]}
            ],
            "max_tokens": 1000,
            "temperature": 0.7
        }
        
        response = bedrock_client.invoke_model(
            modelId=CLAUDE_SONNET_MODEL,
            contentType='application/json',
            accept='application/json',
            body=json.dumps(body)
        )
        
        response_body = json.loads(response['body'].read())
        logger.info("Bedrock 응답 수신 완료")
        
        text = response_body.get('content', [{}])[0].get('text', '')
        
        if not text:
            logger.error('Bedrock 응답에 content[0].text가 없음')
            return create_dummy_quest(club_title)
        
        logger.info(f"Bedrock 응답 텍스트 길이: {len(text)}")
        
        # Extract JSON from response
        import re
        json_match = re.search(r'\{[\s\S]*\}', text)
        if not json_match:
            logger.error('Bedrock 응답에서 JSON을 찾을 수 없음')
            logger.error(f"응답 텍스트: {text}")
            return create_dummy_quest(club_title)
        
        quest = json.loads(json_match.group(0))
        logger.info(f"✅ 퀘스트 생성 완료: {quest['questTitle']}")
        return quest
        
    except Exception as e:
        logger.error(f"퀘스트 생성 실패: {e}", exc_info=True)
        logger.warning("Bedrock 생성 실패로 더미 퀘스트 사용")
        return create_dummy_quest(club_title)

def create_dummy_quest(club_title: str) -> Dict[str, Any]:
    """더미 퀘스트 생성"""
    dummy_quest = {
        "questTitle": f"{club_title} 기본 퀘스트",
        "questDescription": f"{club_title}에 대한 기본적인 학습 퀘스트입니다.",
        "questDifficulthy": 2
    }
    logger.info(f"더미 퀘스트 생성: {dummy_quest}")
    return dummy_quest

# ─────────────────────────────────────────────────────────────
# Strands Agents
# ─────────────────────────────────────────────────────────────

class QuestGeneratorAgent:
    """Quest generation agent using Strands"""
    
    def __init__(self):
        logger.info("QuestGeneratorAgent 초기화")
        # Strands Agent 초기화 - 올바른 API 사용
        self.agent = Agent(
            tools=[
                ensure_index,
                search_today_quests,
                index_quests,
                get_random_files_from_kb,
                generate_quest_from_markdown
            ],
            system_prompt="""
당신은 퀘스트 생성 전문 에이전트입니다. 
사용자가 요청하면 다음 단계를 따라 퀘스트를 생성하거나 검색합니다:

1. 먼저 search_today_quests를 사용해 오늘자 퀘스트가 있는지 확인
2. 퀘스트가 없으면 get_random_files_from_kb로 관련 문서 조회
3. 각 문서에 대해 generate_quest_from_markdown으로 퀘스트 생성
4. ensure_index로 인덱스 확인 후 index_quests로 저장
5. 최종 결과 반환

항상 한국어로 응답하고, 과정을 단계별로 설명해주세요.
            """.strip()
        )
    
    @log_function_execution
    async def generate_quest(self, user: str, club_id: str, club_title: str) -> Dict[str, Any]:
        """Generate or retrieve quests for a user and club"""
        logger.info(f"퀘스트 생성/조회 시작 - user: {user}, clubId: {club_id}, clubTitle: {club_title}")
        
        try:
            # 기존 퀘스트 검색
            logger.info("1단계: 기존 퀘스트 검색")
            quest_list = await search_today_quests(str(user), str(club_id))
            
            if quest_list:
                logger.info(f"기존 퀘스트 {len(quest_list)}개 발견 - 반환")
                return {
                    "user": user,
                    "questList": quest_list
                }
            
            # 새 퀘스트 생성
            logger.info("2단계: 새 퀘스트 생성 시작")
            files = await get_random_files_from_kb(club_title, 3)
            logger.info(f"Knowledge Base에서 {len(files)}개 파일 획득")
            
            generated_quests = []
            today_timestamp = get_today_timestamp()
            
            logger.info("3단계: 각 파일로부터 퀘스트 생성")
            for i, file in enumerate(files):
                logger.info(f"파일 {i+1}/{len(files)} 처리 중")
                quest = await generate_quest_from_markdown(file['content']['text'], club_title)
                formatted_quest = {
                    "user": str(user),
                    "date": today_timestamp,
                    "clubId": str(club_id),
                    "clubTitle": club_title,
                    "questTitle": quest["questTitle"],
                    "questDescription": quest["questDescription"],
                    "questDifficulthy": quest["questDifficulthy"]
                }
                generated_quests.append(formatted_quest)
                logger.info(f"퀘스트 생성 완료: {quest['questTitle']}")
            
            # Save to OpenSearch
            logger.info("4단계: OpenSearch에 저장")
            await ensure_index()
            await index_quests(generated_quests)
            
            logger.info(f"✅ 퀘스트 생성/저장 완료 - 총 {len(generated_quests)}개")
            return {
                "user": user,
                "questList": generated_quests
            }
            
        except Exception as e:
            logger.error(f"퀘스트 생성 전체 프로세스 실패: {e}", exc_info=True)
            raise e

class FeedbackAgent:
    """Feedback handling agent"""
    
    def __init__(self):
        logger.info("FeedbackAgent 초기화")
        # 단순한 에이전트 - 주로 Lambda 호출만 함
        self.agent = Agent(
            system_prompt="""
당신은 피드백 처리 전문 에이전트입니다.
사용자의 퀘스트 피드백을 받아 strands agent2로 전달합니다.
항상 한국어로 응답하세요.
            """.strip()
        )
    
    @log_function_execution
    async def handle_feedback(self, request_body: Dict[str, Any]) -> Dict[str, Any]:
        """Handle quest feedback by calling strands agent2"""
        logger.info("피드백 처리 시작 - strands agent2 호출")
        logger.info(f"전달할 요청 본문: {json.dumps(request_body, ensure_ascii=False)}")
        
        try:
            # Call Lambda function (strands agent2)
            logger.info("Lambda 함수 호출 시작")
            response = lambda_client.invoke(
                FunctionName='arn:aws:lambda:us-east-1:229816860374:function:strands2-lambda',
                InvocationType='RequestResponse',
                Payload=json.dumps(request_body)
            )
            
            logger.info(f"Lambda 응답 상태 코드: {response['StatusCode']}")
            
            if response.get('FunctionError'):
                logger.error(f"Strands Agent2 Lambda 함수 오류: {response['FunctionError']}")
                raise Exception(f"Strands Agent2 Lambda 오류: {response['FunctionError']}")
            
            result = json.loads(response['Payload'].read())
            logger.info("✅ strands agent2 호출 완료")
            logger.info(f"응답 결과: {json.dumps(result, ensure_ascii=False)}")
            return result
            
        except Exception as e:
            logger.error(f"피드백 처리 실패: {e}", exc_info=True)
            raise e

# ─────────────────────────────────────────────────────────────
# Lambda Handler (AWS Lambda compatible)
# ─────────────────────────────────────────────────────────────

@log_function_execution
def lambda_handler(event, context):
    """AWS Lambda handler"""
    logger.info("=" * 60)
    logger.info("🚀 Strands Agent1 Lambda 시작")
    logger.info("=" * 60)
    
    # 요청 정보 상세 로깅
    logger.info(f"📥 수신된 이벤트 (타입: {type(event)})")
    logger.info(f"이벤트 크기: {len(str(event))} 문자")
    logger.info(f"이벤트 내용: {json.dumps(event, ensure_ascii=False, indent=2)}")
    
    # Context 정보 로깅
    if context:
        logger.info(f"📋 Lambda Context - RequestId: {context.aws_request_id}")
        logger.info(f"Function Name: {context.function_name}")
        logger.info(f"Memory Limit: {context.memory_limit_in_mb}MB")
        logger.info(f"Time Remaining: {context.get_remaining_time_in_millis()}ms")
    
    # Environment variables check
    logger.info("🔧 환경변수 확인")
    logger.info(f"  AWS_REGION: {AWS_REGION}")
    logger.info(f"  OPENSEARCH_HOST: {'✅ 설정됨' if OPENSEARCH_HOST else '❌ 설정안됨'}")
    logger.info(f"  KB_ID: {'✅ 설정됨' if KB_ID else '❌ 설정안됨'}")
    logger.info(f"  CLAUDE_SONNET_MODEL: {'✅ 설정됨' if CLAUDE_SONNET_MODEL else '❌ 설정안됨'}")
    
    try:
        # Parse event
        logger.info("📝 이벤트 파싱 시작")
        path, http_method, request_body = parse_event(event)
        
        logger.info(f"파싱 결과:")
        logger.info(f"  경로: {path}")
        logger.info(f"  HTTP 메서드: {http_method}")
        logger.info(f"  요청 본문: {json.dumps(request_body, ensure_ascii=False)}")
        
        if http_method and http_method != 'POST':
            logger.warning(f"지원하지 않는 HTTP 메서드: {http_method}")
            return create_response(405, {"error": "Method Not Allowed. POST 메서드만 지원됩니다."})
        
        # Route to appropriate handler
        logger.info("🔀 라우팅 처리")
        if not path or 'generateQuest' in path:
            logger.info("generateQuest 경로로 라우팅")
            return asyncio.run(handle_generate_quest_route(request_body))
        elif 'feedbackQuest' in path:
            logger.info("feedbackQuest 경로로 라우팅")
            return asyncio.run(handle_feedback_route(request_body))
        else:
            logger.warning(f"지원하지 않는 경로: {path}")
            return create_response(404, {"error": "지원되지 않는 경로입니다. /generateQuest 또는 /feedbackQuest를 사용하세요."})
            
    except Exception as e:
        logger.error("=" * 60)
        logger.error("💥 Strands Agent1 처리 중 예외 발생")
        logger.error("=" * 60)
        logger.error(f"예외 타입: {type(e).__name__}")
        logger.error(f"예외 메시지: {str(e)}")
        logger.error("예외 상세 정보:", exc_info=True)
        logger.error("=" * 60)
        
        return create_response(500, {
            "error": str(e) or "알 수 없는 오류가 발생했습니다",
            "error_type": type(e).__name__
        })

@log_function_execution
def parse_event(event):
    """Parse Lambda event to extract path, method, and body"""
    logger.info("이벤트 파싱 시작")
    
    # API Gateway/직접 호출 구분 및 path/method 파싱
    path = None
    http_method = None
    request_body = None
    
    # 이벤트 구조 분석
    logger.info(f"이벤트 최상위 키들: {list(event.keys())}")
    
    if event.get('requestContext'):
        logger.info("API Gateway 이벤트 감지")
        request_context = event['requestContext']
        logger.info(f"requestContext 키들: {list(request_context.keys())}")
        
        if request_context.get('resourcePath'):
            path = request_context['resourcePath']
            http_method = event.get('httpMethod')
            logger.info("API Gateway v1 형식")
        elif request_context.get('http'):
            path = request_context['http'].get('path')
            http_method = request_context['http'].get('method')
            logger.info("API Gateway v2 형식")
        else:
            path = request_context.get('routeKey') or event.get('path') or event.get('rawPath')
            http_method = request_context.get('method') or event.get('method')
            logger.info("기타 API Gateway 형식")
    else:
        logger.info("직접 호출 이벤트 감지")
        path = event.get('path', '/generateQuest')
        http_method = event.get('httpMethod', 'POST')
    
    logger.info(f"추출된 경로: {path}")
    logger.info(f"추출된 메서드: {http_method}")
    
    # 요청 본문 파싱
    if event.get('body'):
        logger.info("이벤트에 body 필드 존재")
        if isinstance(event['body'], str):
            logger.info("body가 문자열 형태 - JSON 파싱 시도")
            try:
                request_body = json.loads(event['body'])
                logger.info("JSON 파싱 성공")
            except json.JSONDecodeError as e:
                logger.error(f"JSON 파싱 실패: {e}")
                request_body = event['body']
        else:
            logger.info("body가 이미 객체 형태")
            request_body = event['body']
    else:
        logger.info("body 필드 없음 - 전체 이벤트를 요청 본문으로 사용")
        request_body = event
    
    logger.info(f"최종 요청 본문 타입: {type(request_body)}")
    
    return path, http_method, request_body

@log_function_execution
def create_response(status_code: int, body: Dict[str, Any]):
    """Create Lambda response"""
    logger.info(f"응답 생성 - 상태코드: {status_code}")
    logger.info(f"응답 본문: {json.dumps(body, ensure_ascii=False)}")
    
    response = {
        "statusCode": status_code,
        "headers": {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'POST',
            'Access-Control-Allow-Headers': 'Content-Type'
        },
        "body": json.dumps(body, ensure_ascii=False)
    }
    
    logger.info("응답 생성 완료")
    return response

@log_function_execution
async def handle_generate_quest_route(request_body: Dict[str, Any]):
    """Handle generateQuest route"""
    logger.info("🎯 generateQuest 라우트 처리 시작")
    logger.info(f"요청 본문: {json.dumps(request_body, ensure_ascii=False)}")
    
    # 필수 파라미터 검증
    user = request_body.get('user')
    club_id = request_body.get('clubId')
    club_title = request_body.get('clubTitle')
    
    logger.info(f"파라미터 검증 - user: {user}, clubId: {club_id}, clubTitle: {club_title}")
    
    if not user or not club_id or not club_title:
        missing = []
        if not user: missing.append('user')
        if not club_id: missing.append('clubId')
        if not club_title: missing.append('clubTitle')
        
        logger.error(f"필수 파라미터 누락: {missing}")
        return create_response(400, {
            "error": f"generateQuest 필수 파라미터 누락: {', '.join(missing)}",
            "received_params": {
                "user": user,
                "clubId": club_id, 
                "clubTitle": club_title
            }
        })
    
    try:
        logger.info("QuestGeneratorAgent 인스턴스 생성")
        quest_agent = QuestGeneratorAgent()
        
        logger.info("퀘스트 생성 프로세스 시작")
        result = await quest_agent.generate_quest(user, club_id, club_title)
        
        logger.info("✅ generateQuest 처리 완료")
        logger.info(f"결과: {json.dumps(result, ensure_ascii=False)}")
        return create_response(200, result)
        
    except Exception as e:
        logger.error(f"generateQuest 처리 실패: {e}", exc_info=True)
        return create_response(500, {
            "error": str(e),
            "error_type": type(e).__name__
        })

@log_function_execution
async def handle_feedback_route(request_body: Dict[str, Any]):
    """Handle feedbackQuest route"""
    logger.info("📝 feedbackQuest 라우트 처리 시작")
    logger.info(f"요청 본문: {json.dumps(request_body, ensure_ascii=False)}")
    
    # 필수 파라미터 검증
    required_fields = ['user', 'questTitle', 'clubId', 'clubTitle', 'feedback', 'isLike']
    missing_fields = []
    
    for field in required_fields:
        if field not in request_body or request_body[field] is None:
            missing_fields.append(field)
    
    logger.info(f"파라미터 검증 - 필수 필드: {required_fields}")
    logger.info(f"누락된 필드: {missing_fields}")
    
    if missing_fields:
        logger.error(f"feedbackQuest 필수 파라미터 누락: {missing_fields}")
        return create_response(400, {
            "error": f"feedbackQuest 필수 파라미터 누락: {', '.join(missing_fields)}",
            "required_fields": required_fields,
            "received": request_body
        })
    
    try:
        logger.info("FeedbackAgent 인스턴스 생성")
        feedback_agent = FeedbackAgent()
        
        logger.info("피드백 처리 프로세스 시작")
        result = await feedback_agent.handle_feedback(request_body)
        
        logger.info("✅ feedbackQuest 처리 완료")
        logger.info(f"결과: {json.dumps(result, ensure_ascii=False)}")
        return create_response(200, result)
        
    except Exception as e:
        logger.error(f"feedbackQuest 처리 실패: {e}", exc_info=True)
        return create_response(500, {
            "error": str(e),
            "error_type": type(e).__name__
        })