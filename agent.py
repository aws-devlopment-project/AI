# Strands Agent 기반 퀘스트 시스템
# Requirements: pip install strands-agents boto3 opensearch-py psycopg2-binary

import json
import os
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional

import boto3
import psycopg2
from opensearchpy import OpenSearch, RequestsHttpConnection, AWSV4SignerAuth
from strands import Agent, tool

# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 환경 변수
REGION = os.getenv('AWS_REGION', 'us-east-1')
KB_ID = os.getenv('KB_ID')
OPENSEARCH_HOST = os.getenv('OPENSEARCH_HOST')
RDS_HOST = os.getenv('RDS_HOST')
RDS_DB_NAME = os.getenv('RDS_DB_NAME')
RDS_USERNAME = os.getenv('RDS_USERNAME') 
RDS_PASSWORD = os.getenv('RDS_PASSWORD')

# AWS 클라이언트 초기화
session = boto3.Session(region_name=REGION)
bedrock_agent_runtime = session.client('bedrock-agent-runtime')

# OpenSearch 클라이언트 설정
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

def get_db_connection():
    """RDS PostgreSQL 연결"""
    return psycopg2.connect(
        host=RDS_HOST,
        database=RDS_DB_NAME,
        user=RDS_USERNAME,
        password=RDS_PASSWORD
    )

def get_tomorrow_timestamp() -> int:
    """내일 날짜 타임스탬프 생성"""
    tomorrow = datetime.now() + timedelta(days=1)
    tomorrow = tomorrow.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(tomorrow.timestamp() * 1000)

# ============================================================================
# Strands Agent Tools - LLM이 상황에 따라 선택하여 사용
# ============================================================================

@tool
def query_user_data(user_id: str, club_id: str) -> Dict[str, Any]:
    """
    RDS에서 사용자 데이터를 조회합니다.
    LLM이 사용자의 과거 활동이나 선호도를 파악해야 할 때 사용합니다.
    
    Args:
        user_id: 사용자 ID
        club_id: 클럽 ID
    
    Returns:
        사용자 활동 데이터
    """
    logger.info(f"[TOOL] RDS에서 사용자 데이터 조회 - user: {user_id}, club: {club_id}")
    
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                # 사용자의 과거 퀘스트 완료 이력 조회
                cur.execute("""
                    SELECT quest_type, completion_rate, last_activity, preferences
                    FROM user_quest_history 
                    WHERE user_id = %s AND club_id = %s
                    ORDER BY last_activity DESC LIMIT 10
                """, (user_id, club_id))
                
                history = cur.fetchall()
                
                # 사용자 선호도 조회
                cur.execute("""
                    SELECT difficulty_preference, time_preference, activity_type_preference
                    FROM user_preferences 
                    WHERE user_id = %s
                """, (user_id,))
                
                preferences = cur.fetchone()
                
        return {
            "user_id": user_id,
            "club_id": club_id,
            "quest_history": [
                {
                    "quest_type": h[0],
                    "completion_rate": h[1], 
                    "last_activity": h[2],
                    "preferences": h[3]
                } for h in history
            ],
            "user_preferences": {
                "difficulty": preferences[0] if preferences else "medium",
                "time_preference": preferences[1] if preferences else "evening",
                "activity_type": preferences[2] if preferences else "general"
            } if preferences else None
        }
        
    except Exception as e:
        logger.error(f"RDS 조회 실패: {e}")
        return {"error": f"RDS 조회 실패: {str(e)}"}

@tool 
def search_knowledge_base(club_title: str, query: str = "", count: int = 3) -> List[Dict[str, Any]]:
    """
    Bedrock Knowledge Base에서 관련 문서를 검색합니다.
    LLM이 새로운 퀘스트 아이디어나 참고 자료가 필요할 때 사용합니다.
    
    Args:
        club_title: 클럽 이름
        query: 검색 쿼리 (선택적)
        count: 조회할 문서 수
        
    Returns:
        관련 문서 목록
    """
    logger.info(f"[TOOL] Knowledge Base 검색 - club: {club_title}, query: {query}")
    
    try:
        search_query = f"club: {club_title}"
        if query:
            search_query += f" {query}"
            
        response = bedrock_agent_runtime.retrieve(
            knowledgeBaseId=KB_ID,
            retrievalQuery={'text': search_query},
            retrievalConfiguration={
                'vectorSearchConfiguration': {
                    'numberOfResults': count * 2  # 여유있게 조회
                }
            }
        )
        
        results = response.get('retrievalResults', [])
        
        # 상위 count개만 반환
        selected_results = results[:count]
        
        documents = []
        for result in selected_results:
            content = result['content']['text']
            documents.append({
                "content": content[:1000],  # 내용 제한
                "source": result.get('location', {}).get('s3Location', {}).get('uri', 'unknown'),
                "score": result.get('score', 0)
            })
            
        return documents
        
    except Exception as e:
        logger.error(f"Knowledge Base 검색 실패: {e}")
        return [{"error": f"Knowledge Base 검색 실패: {str(e)}"}]

@tool
def search_existing_quests(user_id: str, club_id: str, date_filter: str = "today") -> List[Dict[str, Any]]:
    """
    OpenSearch에서 기존 퀘스트를 검색합니다.
    LLM이 중복 생성을 피하거나 과거 퀘스트를 참고해야 할 때 사용합니다.
    
    Args:
        user_id: 사용자 ID
        club_id: 클럽 ID  
        date_filter: 날짜 필터 ("today", "yesterday", "week")
        
    Returns:
        기존 퀘스트 목록
    """
    logger.info(f"[TOOL] OpenSearch에서 퀘스트 검색 - user: {user_id}, club: {club_id}, filter: {date_filter}")
    
    try:
        # 날짜 범위 계산
        now = datetime.now()
        if date_filter == "today":
            start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
            end_date = start_date + timedelta(days=1)
        elif date_filter == "yesterday":
            start_date = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
            end_date = start_date + timedelta(days=1)
        elif date_filter == "week":
            start_date = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=7)
            end_date = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        
        start_timestamp = int(start_date.timestamp() * 1000)
        end_timestamp = int(end_date.timestamp() * 1000)
        
        search_query = {
            "query": {
                "bool": {
                    "must": [
                        {"term": {"user": user_id}},
                        {"term": {"clubId": club_id}},
                        {
                            "range": {
                                "date": {
                                    "gte": start_timestamp,
                                    "lt": end_timestamp
                                }
                            }
                        }
                    ]
                }
            },
            "size": 20,
            "sort": [{"date": {"order": "desc"}}]
        }
        
        response = opensearch_client.search(index='quest-knowledge', body=search_query)
        hits = response.get('hits', {}).get('hits', [])
        
        quests = []
        for hit in hits:
            source = hit['_source']
            quests.append({
                "questTitle": source.get('questTitle'),
                "questDescription": source.get('questDescription'),
                "questDifficulthy": source.get('questDifficulthy'),
                "date": source.get('date'),
                "clubTitle": source.get('clubTitle')
            })
            
        return quests
        
    except Exception as e:
        logger.error(f"OpenSearch 검색 실패: {e}")
        return [{"error": f"OpenSearch 검색 실패: {str(e)}"}]

@tool
def save_generated_quests(user_id: str, club_id: str, club_title: str, quests: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    생성된 퀘스트를 OpenSearch에 저장합니다.
    LLM이 퀘스트 생성을 완료한 후 결과를 저장할 때 사용합니다.
    
    Args:
        user_id: 사용자 ID
        club_id: 클럽 ID
        club_title: 클럽 이름
        quests: 저장할 퀘스트 목록
        
    Returns:
        저장 결과
    """
    logger.info(f"[TOOL] 퀘스트 저장 - user: {user_id}, club: {club_id}, count: {len(quests)}")
    
    try:
        tomorrow_timestamp = get_tomorrow_timestamp()
        
        # 퀘스트 문서 준비
        documents = []
        for i, quest in enumerate(quests):
            doc = {
                "user": user_id,
                "clubId": club_id,
                "clubTitle": club_title,
                "date": tomorrow_timestamp,
                "questTitle": quest.get("questTitle", f"퀘스트 {i+1}"),
                "questDescription": quest.get("questDescription", ""),
                "questDifficulthy": quest.get("questDifficulthy", 2)
            }
            documents.append(doc)
        
        # 벌크 저장
        body = []
        for doc in documents:
            doc_id = f"{user_id}::{club_id}::{tomorrow_timestamp}::{doc['questTitle']}"
            body.append({"index": {"_index": "quest-knowledge", "_id": doc_id}})
            body.append(doc)
        
        response = opensearch_client.bulk(body=body)
        
        if response.get('errors'):
            error_items = [item for item in response['items'] if item.get('index', {}).get('error')]
            logger.error(f"저장 중 일부 오류: {error_items}")
            return {"success": False, "error": "일부 퀘스트 저장 실패"}
        
        logger.info(f"퀘스트 저장 완료 - {len(documents)}개")
        return {
            "success": True,
            "saved_count": len(documents),
            "quest_date": tomorrow_timestamp,
            "message": f"{len(documents)}개 퀘스트가 성공적으로 저장되었습니다"
        }
        
    except Exception as e:
        logger.error(f"퀘스트 저장 실패: {e}")
        return {"success": False, "error": f"저장 실패: {str(e)}"}

# ============================================================================
# Strands Agent 정의 - LLM이 도구들을 지능적으로 사용
# ============================================================================

# Agent 생성 - LLM이 상황에 따라 도구를 선택하여 사용
quest_agent = Agent(
    tools=[
        query_user_data,      # 1. RDS에서 사용자 데이터 조회
        search_knowledge_base, # 2. Knowledge Base 검색  
        search_existing_quests, # 3. OpenSearch에서 기존 퀘스트 검색
        save_generated_quests  # 4. 생성된 퀘스트 저장
    ],
    system_prompt="""
당신은 지능형 퀘스트 생성 에이전트입니다. 

사용자 요청을 받으면 다음과 같이 상황을 판단하여 적절한 도구들을 선택하고 사용하세요:

1. **상황 분석**: 먼저 어떤 작업이 필요한지 판단하세요
   - 새로운 퀘스트 생성인가?
   - 기존 퀘스트 조회인가?
   - 피드백 기반 개선인가?

2. **정보 수집**: 필요에 따라 정보를 수집하세요
   - 사용자 이력이 필요하면 query_user_data 사용
   - 참고 자료가 필요하면 search_knowledge_base 사용  
   - 중복 확인이 필요하면 search_existing_quests 사용

3. **퀘스트 생성**: 수집된 정보를 바탕으로 퀘스트를 생성하세요
   - 조건부 행동 구조(IF-THEN) 필수 사용
   - 클럽 주제와 관련된 조건만 사용
   - 사용자 선호도와 이력 반영
   - 3개의 서로 다른 난이도 퀘스트 생성

4. **저장**: 생성된 퀘스트를 save_generated_quests로 저장하세요

중요한 원칙:
- 사용자가 3초 안에 확인할 수 있는 일상적 조건만 사용
- 각 퀘스트는 반드시 "만약 [조건]이라면 [행동A], 그렇지 않다면 [행동B]" 형식
- 클럽 주제와 관련된 조건 사용 (운동클럽→운동조건, 금융클럽→지출조건 등)
- 과거 성공/실패 패턴을 고려하여 개인화

작업 과정을 사용자에게 명확히 설명하세요.
"""
)

# ============================================================================
# Lambda Handler - API Gateway 요청 처리
# ============================================================================

def lambda_handler(event, context):
    """AWS Lambda 핸들러 - Strands Agent가 모든 요청을 지능적으로 처리"""
    logger.info("=== Strands Agent 퀘스트 시스템 시작 ===")
    
    try:
        # 요청 파싱
        if 'body' in event and event['body']:
            request_body = json.loads(event['body'])
        else:
            request_body = event
            
        # 필수 파라미터 확인
        user_id = request_body.get('user')
        club_id = request_body.get('clubId') 
        club_title = request_body.get('clubTitle')
        request_type = request_body.get('requestType', 'generateQuest')
        
        if not all([user_id, club_id, club_title]):
            return create_response(400, {
                "error": "필수 파라미터 누락: user, clubId, clubTitle"
            })
        
        # Agent에게 전달할 프롬프트 생성
        if request_type == 'generateQuest':
            user_prompt = f"""
새로운 퀘스트를 생성해주세요.

사용자 정보:
- 사용자 ID: {user_id}
- 클럽 ID: {club_id}
- 클럽 이름: {club_title}

다음 단계로 작업해주세요:
1. 먼저 이 사용자의 오늘 퀘스트가 이미 있는지 확인
2. 있다면 기존 퀘스트 반환, 없다면 새로 생성
3. 새로 생성할 때는 사용자 이력과 선호도 파악
4. 클럽 관련 참고 자료 검색
5. 조건부 구조의 퀘스트 3개 생성 (쉬움/보통/어려움)
6. 생성된 퀘스트 저장

각 단계에서 어떤 도구를 사용했는지 설명해주세요.
"""
        
        elif request_type == 'feedbackQuest':
            feedback = request_body.get('feedback', '')
            is_like = request_body.get('isLike', True)
            quest_title = request_body.get('questTitle', '')
            
            user_prompt = f"""
사용자 피드백을 반영하여 퀘스트를 개선해주세요.

피드백 정보:
- 사용자 ID: {user_id}
- 클럽: {club_title}
- 기존 퀘스트: {quest_title}
- 피드백: {feedback}
- 만족도: {'만족' if is_like else '불만족'}

다음 단계로 작업해주세요:
1. 사용자의 과거 퀘스트 이력 분석
2. 피드백 내용에 따른 개선 방향 결정
3. 만족스러웠다면 유사한 스타일로, 불만족이었다면 다른 접근으로
4. 개선된 퀘스트 3개 생성 (기본/표준/심화)
5. 생성된 퀘스트 저장

피드백을 어떻게 반영했는지 설명해주세요.
"""
        
        else:
            return create_response(400, {"error": "지원하지 않는 요청 타입"})
        
        # Strands Agent 실행 - LLM이 상황을 판단하여 적절한 도구들을 사용
        logger.info("Strands Agent 실행 중...")
        response = quest_agent(user_prompt)
        
        logger.info("Strands Agent 처리 완료")
        
        # 응답 처리
        return create_response(200, {
            "success": True,
            "message": "퀘스트 처리가 완료되었습니다",
            "agent_response": str(response),
            "user_id": user_id,
            "club_title": club_title
        })
        
    except Exception as e:
        logger.error(f"처리 실패: {e}")
        return create_response(500, {
            "success": False,
            "error": str(e)
        })

def create_response(status_code: int, body: Dict[str, Any]):
    """API Gateway 응답 생성"""
    return {
        "statusCode": status_code,
        "headers": {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'POST',
            'Access-Control-Allow-Headers': 'Content-Type'
        },
        "body": json.dumps(body, ensure_ascii=False)
    }

# ============================================================================
# 로컬 테스트
# ============================================================================

if __name__ == "__main__":
    # 로컬 테스트 이벤트
    test_event = {
        "user": "test_user_001",
        "clubId": "1",
        "clubTitle": "동네탐험대",
        "requestType": "generateQuest"
    }
    
    class MockContext:
        def __init__(self):
            self.function_name = "strands-quest-agent"
            self.remaining_time_in_millis = 30000
    
    # Agent 테스트 실행
    result = lambda_handler(test_event, MockContext())
    print(json.dumps(result, ensure_ascii=False, indent=2))