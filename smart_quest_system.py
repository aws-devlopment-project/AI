import asyncio
import json
import re
import os
import boto3
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

# 환경변수 로드
def load_env_file(file_path: str = "aws.env"):
    """aws.env 파일에서 환경변수 로드"""
    if os.path.exists(file_path):
        with open(file_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    if '=' in line:
                        key, value = line.split('=', 1)
                        os.environ[key] = value
        print(f"✅ 환경변수 로드 완료: {file_path}")
    else:
        print(f"❌ 환경변수 파일 없음: {file_path}")

@dataclass
class UserFeedback:
    """사용자 피드백 데이터 클래스"""
    content: str
    timestamp: str
    user_id: Optional[str] = None
    category_hint: Optional[str] = None

@dataclass
class ExpertDocument:
    """전문가 문서 데이터 클래스"""
    document_id: str
    topic: str
    content: str
    metadata: Dict
    quality_score: float
    created_at: str

@dataclass
class Quest:
    """퀘스트 데이터 클래스"""
    title: str
    if_condition: str
    then_action: str
    estimated_time: str
    difficulty: str
    success_metric: str
    rag_evidence: str

class SmartQuestSystem:
    """통합 지능형 퀘스트 생성 시스템"""
    
    def __init__(self):
        print("🚀 Smart Quest System 초기화 시작...")
        
        # 환경변수 로드
        load_env_file("aws.env")
        
        # AWS 설정
        self.region = "us-east-1"
        self.model_id = "us.anthropic.claude-3-5-sonnet-20241022-v2:0"
        
        # 시스템 상태 초기화
        self.system_ready = False
        
        # 컴포넌트 초기화
        self._initialize_components()
        
        print("✅ Smart Quest System 초기화 완료!")
        self._print_system_info()
        
        # 최종 상태 출력
        print(f"🔧 시스템 준비 상태: {'✅ 준비 완료' if self.system_ready else '❌ 준비 안됨'}")

    def _initialize_components(self):
        """모든 시스템 컴포넌트 초기화"""
        
        print("🔧 시스템 컴포넌트 초기화 중...")
        
        try:
            # OpenSearch 클라이언트 (내부 문서 검색)
            self.opensearch_client = OpenSearchClient()
            print("   ✅ OpenSearch 클라이언트 초기화")
            
            # Kendra 클라이언트 (외부 신뢰 소스)
            self.kendra_client = KendraClient(region=self.region)
            print("   ✅ Kendra 클라이언트 초기화")
            
            # Strands Agent 시스템 (동적 전문가)
            self.agent_system = StrandsAgentSystem(
                model_id=self.model_id,
                region=self.region
            )
            print("   ✅ Strands Agent 시스템 초기화")
            
            # DynamoDB 클라이언트 (문서 저장)
            self.dynamodb_client = DynamoDBClient(region=self.region)
            print("   ✅ DynamoDB 클라이언트 초기화")
            
            # RAG 퀘스트 생성기
            self.rag_generator = RAGQuestGenerator(
                model_id=self.model_id,
                region=self.region
            )
            print("   ✅ RAG 퀘스트 생성기 초기화")
            
            self.system_ready = True
            print("   🎉 모든 컴포넌트 초기화 완료!")
            
        except Exception as e:
            print(f"   ❌ 컴포넌트 초기화 실패: {e}")
            import traceback
            traceback.print_exc()
            self.system_ready = False

    def _print_system_info(self):
        """시스템 정보 출력"""
        print("\n📋 시스템 구성:")
        print("   🔍 OpenSearch: 내부 문서 유사도 검색")
        print("   🌐 Kendra: 외부 신뢰 소스 검색 (NIH, WHO, SEC, FDA)")
        print("   🤖 Strands Agents: 동적 전문가 팀 생성")
        print("   💾 DynamoDB: 고품질 전문가 문서 저장")
        print("   🎮 RAG Generator: 개인화 퀘스트 생성")
        print(f"   🧠 AI Model: {self.model_id}")
        print(f"   🌍 AWS Region: {self.region}")

    async def process_user_feedback(self, feedback: UserFeedback) -> Dict:
        """
        사용자 피드백 처리 메인 워크플로우
        
        1. OpenSearch로 유사 문서 검색
        2. 유사도 판단
        3a. 충분한 유사 문서 있음 → 기존 문서로 RAG 퀘스트 생성
        3b. 유사 문서 없음/애매 → 새 전문가 문서 생성 → RAG 퀘스트 생성
        """
        
        if not self.system_ready:
            return {"success": False, "error": "시스템이 준비되지 않았습니다."}
        
        print(f"\n🎯 사용자 피드백 처리 시작")
        print(f"📝 피드백: {feedback.content}")
        print("="*60)
        
        try:
            # 1단계: OpenSearch로 유사 문서 검색
            print("🔍 1단계: 기존 문서 유사도 검색...")
            similar_docs = await self.opensearch_client.find_similar_documents(
                query=feedback.content,
                threshold=0.7,  # 유사도 임계값
                limit=5
            )
            
            # 2단계: 유사도 판단
            sufficient_match = self._evaluate_similarity(similar_docs, feedback)
            
            if sufficient_match:
                print(f"   ✅ 유사 문서 {len(similar_docs)}개 발견 - 기존 문서 활용")
                return await self._generate_quest_from_existing(similar_docs, feedback)
            else:
                print("   ⚠️ 충분한 유사 문서 없음 - 새 전문가 문서 생성 필요")
                return await self._create_new_expert_document_and_quest(feedback)
                
        except Exception as e:
            print(f"\n❌ 피드백 처리 실패: {e}")
            import traceback
            traceback.print_exc()
            
            return {
                "success": False,
                "error": str(e),
                "fallback_quest": self._create_fallback_quest(feedback)
            }

    def _evaluate_similarity(self, similar_docs: List[ExpertDocument], feedback: UserFeedback) -> bool:
        """유사도 평가 - 기존 문서로 충분한지 판단"""
        
        if not similar_docs:
            print("   📊 유사 문서 없음")
            return False
        
        # 최고 유사도 문서 확인
        best_match = similar_docs[0]
        similarity_score = best_match.metadata.get('similarity_score', 0.0)
        
        print(f"   📊 최고 유사도: {similarity_score:.3f}")
        print(f"   📄 최고 유사 문서: {best_match.document_id}")
        
        # 유사도 기준
        if similarity_score >= 0.85:
            print("   ✅ 높은 유사도 - 기존 문서 활용")
            return True
        elif similarity_score >= 0.70:
            print("   🤔 중간 유사도 - 품질 검토 필요")
            return len(similar_docs) >= 2  # 여러 문서가 있으면 활용
        else:
            print("   ❌ 낮은 유사도 - 새 문서 생성 필요")
            return False

    async def _generate_quest_from_existing(self, similar_docs: List[ExpertDocument], feedback: UserFeedback) -> Dict:
        """기존 문서를 활용한 RAG 퀘스트 생성"""
        
        print("🎮 기존 문서 기반 RAG 퀘스트 생성...")
        
        try:
            result = await self.rag_generator.generate_from_documents(
                documents=similar_docs,
                user_feedback=feedback
            )
            
            print("   ✅ RAG 퀘스트 생성 완료")
            
            return {
                "success": True,
                "method": "existing_documents",
                "documents_used": len(similar_docs),
                "quests": result["quests"],
                "metadata": {
                    "processing_time": datetime.now().isoformat(),
                    "similarity_scores": [doc.metadata.get('similarity_score', 0) for doc in similar_docs]
                }
            }
            
        except Exception as e:
            print(f"   ❌ RAG 퀘스트 생성 실패: {e}")
            # 폴백: 새 문서 생성 시도
            return await self._create_new_expert_document_and_quest(feedback)

    async def _create_new_expert_document_and_quest(self, feedback: UserFeedback) -> Dict:
        """새로운 전문가 문서 생성 후 RAG 퀘스트 생성"""
        
        print("📝 새로운 전문가 문서 생성 프로세스 시작...")
        
        try:
            # 1. Kendra 신뢰 소스 검색
            print("🌐 Kendra 신뢰 소스 검색...")
            trusted_sources = await self.kendra_client.search_trusted_sources(
                query=feedback.content,
                category=self._determine_category(feedback.content)
            )
            
            # 2. 동적 전문가 팀 생성 및 협업
            print("🤖 동적 전문가 팀 생성...")
            expert_document = await self.agent_system.create_expert_document(
                user_feedback=feedback,
                trusted_sources=trusted_sources
            )
            
            # 3. DynamoDB에 저장
            print("💾 전문가 문서 저장...")
            document_id = await self.dynamodb_client.save_expert_document(expert_document)
            
            # 4. OpenSearch 인덱스 업데이트
            print("🔄 OpenSearch 인덱스 업데이트...")
            await self.opensearch_client.index_document(expert_document)
            
            # 5. RAG 퀘스트 생성
            print("🎮 RAG 퀘스트 생성...")
            quest_result = await self.rag_generator.generate_from_documents(
                documents=[expert_document],
                user_feedback=feedback
            )
            
            print("   ✅ 새 전문가 문서 + RAG 퀘스트 생성 완료")
            
            return {
                "success": True,
                "method": "new_expert_document",
                "document_id": document_id,
                "expert_document": expert_document,
                "quests": quest_result["quests"],
                "metadata": {
                    "processing_time": datetime.now().isoformat(),
                    "trusted_sources_count": len(trusted_sources),
                    "expert_collaboration": True
                }
            }
            
        except Exception as e:
            print(f"   ❌ 새 문서 생성 실패: {e}")
            return {
                "success": False,
                "error": str(e),
                "fallback_quest": self._create_fallback_quest(feedback)
            }

    def _determine_category(self, content: str) -> str:
        """피드백 내용에서 카테고리 결정"""
        content_lower = content.lower()
        
        health_keywords = ['목', '어깨', '허리', '통증', '건강', '운동', '스트레칭', '몸']
        finance_keywords = ['돈', '예산', '투자', '저축', '재정', '금융', '주식', '펀드']
        skincare_keywords = ['피부', '스킨케어', '화장품', '세안', '로션', '크림', '여드름']
        
        if any(keyword in content_lower for keyword in health_keywords):
            return "health"
        elif any(keyword in content_lower for keyword in finance_keywords):
            return "finance"
        elif any(keyword in content_lower for keyword in skincare_keywords):
            return "skincare"
        else:
            return "health"  # 기본값

    def _create_fallback_quest(self, feedback: UserFeedback) -> List[Quest]:
        """폴백 퀘스트 생성"""
        category = self._determine_category(feedback.content)
        
        return [
            Quest(
                title=f"기본 {category} 실천하기",
                if_condition="하루 중 적절한 시간에",
                then_action=f"{category} 관련 기본 행동 실행",
                estimated_time="10분",
                difficulty="쉬움",
                success_metric="일일 실행 여부",
                rag_evidence="기본 권장사항"
            )
        ]

# Mock 컴포넌트들 - 실제 동작하는 가짜 버전들
class OpenSearchClient:
    """OpenSearch 클라이언트 - Mock 버전"""
    
    def __init__(self):
        print("   🔍 OpenSearch 클라이언트 설정... (Mock)")
        # Mock 데이터베이스 (메모리)
        self.mock_documents = []
        self._initialize_mock_data()
        
    def _initialize_mock_data(self):
        """Mock 문서 데이터 초기화"""
        self.mock_documents = [
            ExpertDocument(
                document_id="health_neck_001",
                topic="health", 
                content="목 통증 완화를 위한 전문가 가이드: 물리치료사 3명이 협업하여 작성한 종합 가이드입니다. 목을 좌우로 천천히 돌리는 스트레칭을 15초씩 3-5회 반복하면 효과적입니다.",
                metadata={"experts": ["물리치료사", "운동전문가"], "quality_score": 0.95},
                quality_score=0.95,
                created_at="2024-12-14T10:30:00Z"
            ),
            ExpertDocument(
                document_id="finance_budget_001", 
                topic="finance",
                content="20대 직장인을 위한 예산 관리 전문가 가이드: 재정 전문가 3명이 협업하여 작성했습니다. 50/30/20 규칙을 활용하여 수입의 50%는 필수지출, 30%는 선택지출, 20%는 저축으로 배분하세요.",
                metadata={"experts": ["재정전문가", "투자컨설턴트"], "quality_score": 0.92},
                quality_score=0.92,
                created_at="2024-12-14T09:15:00Z"
            ),
            ExpertDocument(
                document_id="skincare_acne_001",
                topic="skincare",
                content="여드름 관리를 위한 피부과 전문의 가이드: 피부과 전문의와 화장품 연구원이 협업하여 작성했습니다. 순한 세안제로 하루 2회 세안하고, 살리실산 성분의 제품을 점진적으로 사용하세요.",
                metadata={"experts": ["피부과전문의", "화장품연구원"], "quality_score": 0.93},
                quality_score=0.93,
                created_at="2024-12-14T08:45:00Z"
            )
        ]
        
    async def find_similar_documents(self, query: str, threshold: float = 0.7, limit: int = 5) -> List[ExpertDocument]:
        """유사 문서 검색 - Mock 구현"""
        print(f"      검색어: {query}")
        print(f"      임계값: {threshold}")
        
        # 간단한 키워드 매칭으로 유사도 계산
        results = []
        
        for doc in self.mock_documents:
            similarity = self._calculate_mock_similarity(query, doc.content)
            if similarity >= threshold:
                # 메타데이터에 유사도 추가
                doc.metadata['similarity_score'] = similarity
                results.append(doc)
        
        # 유사도 순으로 정렬
        results.sort(key=lambda x: x.metadata['similarity_score'], reverse=True)
        
        print(f"      유사 문서 {len(results)}개 발견")
        for doc in results[:limit]:
            print(f"         {doc.document_id}: {doc.metadata['similarity_score']:.3f}")
        
        return results[:limit]
    
    def _calculate_mock_similarity(self, query: str, content: str) -> float:
        """Mock 유사도 계산 - 단순 키워드 매칭"""
        query_lower = query.lower()
        content_lower = content.lower()
        
        # 키워드 기반 유사도
        if "목" in query_lower and "목" in content_lower:
            return 0.9
        elif "통증" in query_lower and "통증" in content_lower:
            return 0.85
        elif "운동" in query_lower and ("운동" in content_lower or "스트레칭" in content_lower):
            return 0.8
        elif "예산" in query_lower and "예산" in content_lower:
            return 0.9
        elif "돈" in query_lower and ("예산" in content_lower or "재정" in content_lower):
            return 0.85
        elif "피부" in query_lower and "피부" in content_lower:
            return 0.9
        elif "여드름" in query_lower and "여드름" in content_lower:
            return 0.95
        else:
            return 0.3  # 낮은 유사도
    
    async def index_document(self, document: ExpertDocument):
        """문서 인덱싱 - Mock 구현"""
        print(f"      문서 인덱싱: {document.document_id}")
        # Mock 데이터베이스에 추가
        self.mock_documents.append(document)
        print(f"      총 문서 수: {len(self.mock_documents)}개")

class KendraClient:
    """Kendra 클라이언트 - Mock 버전"""
    
    def __init__(self, region: str):
        self.region = region
        print(f"   🌐 Kendra 클라이언트 설정 (리전: {region})... (Mock)")
        
    async def search_trusted_sources(self, query: str, category: str) -> List[Dict]:
        """신뢰 소스 검색 - Mock 구현"""
        print(f"      카테고리: {category}")
        print(f"      검색어: {query}")
        
        # 카테고리별 Mock 신뢰 소스 데이터
        mock_sources = {
            "health": [
                {
                    "title": "Neck Pain Relief Exercises - NIH Guidelines",
                    "content": "The National Institute of Health recommends gentle neck rotations held for 15 seconds, performed 3-5 times daily for optimal neck pain relief.",
                    "source": "https://www.nih.gov/neck-pain-relief",
                    "confidence": "VERY_HIGH"
                },
                {
                    "title": "Physical Therapy for Cervical Pain - Mayo Clinic",
                    "content": "Mayo Clinic research shows that progressive neck strengthening exercises significantly reduce chronic neck pain when performed consistently.",
                    "source": "https://www.mayoclinic.org/neck-exercises",
                    "confidence": "HIGH"
                }
            ],
            "finance": [
                {
                    "title": "Budget Planning Guidelines - Federal Reserve",
                    "content": "Federal Reserve economic data supports the 50/30/20 budgeting rule for young adults: 50% needs, 30% wants, 20% savings.",
                    "source": "https://www.federalreserve.gov/budget-guidelines",
                    "confidence": "VERY_HIGH"
                },
                {
                    "title": "Young Adult Financial Planning - SEC",
                    "content": "SEC guidelines recommend starting emergency fund building and retirement savings in your 20s for long-term financial stability.",
                    "source": "https://www.sec.gov/young-adult-finance",
                    "confidence": "HIGH"
                }
            ],
            "skincare": [
                {
                    "title": "Acne Treatment Guidelines - FDA",
                    "content": "FDA approved acne treatments include salicylic acid and benzoyl peroxide. Start with lower concentrations to avoid irritation.",
                    "source": "https://www.fda.gov/acne-treatment",
                    "confidence": "VERY_HIGH"
                },
                {
                    "title": "Dermatological Care - American Academy of Dermatology",
                    "content": "AAD recommends gentle cleansing twice daily and gradual introduction of active ingredients for effective acne management.",
                    "source": "https://www.aad.org/acne-care",
                    "confidence": "HIGH"
                }
            ]
        }
        
        sources = mock_sources.get(category, [])
        print(f"      신뢰 소스 {len(sources)}개 검색됨")
        
        return sources

class StrandsAgentSystem:
    """Strands Agent 시스템 - Mock 버전"""
    
    def __init__(self, model_id: str, region: str):
        self.model_id = model_id
        self.region = region
        print(f"   🤖 Strands Agent 시스템 설정... (Mock)")
        
    async def create_expert_document(self, user_feedback: UserFeedback, trusted_sources: List[Dict]) -> ExpertDocument:
        """전문가 협업으로 문서 생성 - Mock 구현"""
        print(f"      전문가 팀 생성 및 협업 시작...")
        
        # 카테고리 결정
        category = self._determine_category(user_feedback.content)
        
        # Mock 전문가 협업 결과
        mock_expert_content = self._generate_mock_expert_content(user_feedback, trusted_sources, category)
        
        document = ExpertDocument(
            document_id=f"{category}_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            topic=category,
            content=mock_expert_content,
            metadata={
                "user_request": user_feedback.content,
                "trusted_sources_count": len(trusted_sources),
                "expert_collaboration": True,
                "generated_by": "mock_strands_agent"
            },
            quality_score=0.94,
            created_at=datetime.now().isoformat()
        )
        
        print(f"      전문가 문서 생성 완료: {document.document_id}")
        
        return document
    
    def _determine_category(self, content: str) -> str:
        """카테고리 결정"""
        content_lower = content.lower()
        
        if any(word in content_lower for word in ['목', '어깨', '허리', '통증', '건강', '운동', '스트레칭']):
            return "health"
        elif any(word in content_lower for word in ['돈', '예산', '투자', '저축', '재정', '금융']):
            return "finance"
        elif any(word in content_lower for word in ['피부', '스킨케어', '화장품', '여드름']):
            return "skincare"
        else:
            return "health"
    
    def _generate_mock_expert_content(self, user_feedback: UserFeedback, trusted_sources: List[Dict], category: str) -> str:
        """Mock 전문가 협업 내용 생성"""
        
        # 신뢰 소스 요약
        sources_summary = "\n".join([f"- {source['title']}: {source['content'][:100]}..." for source in trusted_sources])
        
        mock_content = f"""
# {category.title()} 전문가 협업 문서

## 사용자 요청 분석
{user_feedback.content}

## 신뢰할 수 있는 최신 정보
{sources_summary}

## 전문가 팀 협업 결과

### 1. 수석 전문가 분석
사용자의 요청을 종합적으로 분석한 결과, 다음과 같은 접근이 필요합니다:
- 과학적 근거 기반의 안전한 방법
- 개인의 상황에 맞는 맞춤형 솔루션
- 점진적이고 지속 가능한 접근

### 2. 실무 전문가 권고사항
구체적인 실행 방법:
- 단계별 실행 계획
- 주의사항 및 안전 수칙
- 효과 측정 방법

### 3. 검증 전문가 의견
안전성 및 효과성 검토:
- 과학적 근거 확인
- 부작용 위험 평가
- 개선 및 수정 사항

## 최종 권고사항
전문가 팀의 합의된 최종 권고사항으로, 사용자가 안전하고 효과적으로 실행할 수 있는 방법을 제시합니다.

*본 문서는 {len(trusted_sources)}개의 신뢰할 수 있는 소스와 전문가 협업을 통해 생성되었습니다.*
        """.strip()
        
        return mock_content

class DynamoDBClient:
    """DynamoDB 클라이언트 - Mock 버전"""
    
    def __init__(self, region: str):
        self.region = region
        self.mock_storage = {}  # 메모리 저장소
        print(f"   💾 DynamoDB 클라이언트 설정 (리전: {region})... (Mock)")
        
    async def save_expert_document(self, document: ExpertDocument) -> str:
        """전문가 문서 저장 - Mock 구현"""
        print(f"      문서 저장: {document.document_id}")
        
        # Mock 저장소에 저장
        self.mock_storage[document.document_id] = {
            "topic": document.topic,
            "document_id": document.document_id,
            "content": document.content,
            "metadata": document.metadata,
            "quality_score": document.quality_score,
            "created_at": document.created_at
        }
        
        print(f"      총 저장된 문서: {len(self.mock_storage)}개")
        
        return document.document_id

class RAGQuestGenerator:
    """RAG 퀘스트 생성기 - Mock 버전"""
    
    def __init__(self, model_id: str, region: str):
        self.model_id = model_id
        self.region = region
        print(f"   🎮 RAG 퀘스트 생성기 설정... (Mock)")
        
    async def generate_from_documents(self, documents: List[ExpertDocument], user_feedback: UserFeedback) -> Dict:
        """문서 기반 퀘스트 생성 - Mock 구현"""
        print(f"      문서 {len(documents)}개 기반 퀘스트 생성...")
        
        if not documents:
            return {"quests": []}
        
        # 첫 번째 문서 기반으로 Mock 퀘스트 생성
        primary_doc = documents[0]
        category = primary_doc.topic
        
        mock_quests = self._generate_mock_quests(category, user_feedback.content)
        
        print(f"      퀘스트 {len(mock_quests)}개 생성 완료")
        
        return {"quests": mock_quests}
    
    def _generate_mock_quests(self, category: str, user_content: str) -> List[Quest]:
        """카테고리별 Mock 퀘스트 생성"""
        
        quest_templates = {
            "health": [
                Quest(
                    title="아침 목 깨우기",
                    if_condition="매일 아침 기상 직후",
                    then_action="목을 좌우로 천천히 3회씩 돌리고 턱 당기기 10초 유지",
                    estimated_time="3분",
                    difficulty="쉬움",
                    success_metric="일주일에 5일 이상 실행",
                    rag_evidence="물리치료학회 가이드라인에 따른 15초 유지 권장사항"
                ),
                Quest(
                    title="업무 중 목 휴식",
                    if_condition="컴퓨터 작업 2시간마다",
                    then_action="의자에서 목 스트레칭과 어깨 으쓱하기 각 10초씩",
                    estimated_time="5분", 
                    difficulty="보통",
                    success_metric="하루 3회 이상 실행",
                    rag_evidence="재활의학과 전문의 권고사항"
                ),
                Quest(
                    title="저녁 목 이완 루틴",
                    if_condition="잠자리에 들기 30분 전",
                    then_action="목 마사지와 전체 목 스트레칭 루틴 완주",
                    estimated_time="10분",
                    difficulty="어려움", 
                    success_metric="주 4회 이상 지속",
                    rag_evidence="종합 목 관리 전문가 협업 문서"
                )
            ],
            "finance": [
                Quest(
                    title="가계부 작성 시작",
                    if_condition="매일 저녁 식사 후",
                    then_action="하루 지출 내역을 앱에 기록하고 카테고리별 분류",
                    estimated_time="5분",
                    difficulty="쉬움",
                    success_metric="2주 연속 매일 기록",
                    rag_evidence="금융감독원 가계재정 관리 가이드"
                ),
                Quest(
                    title="50/30/20 예산 계획",
                    if_condition="매월 첫째 주 주말",
                    then_action="월 수입 기준 예산을 50/30/20으로 계획하고 목표 설정",
                    estimated_time="30분",
                    difficulty="보통", 
                    success_metric="3개월 연속 예산 달성도 80% 이상",
                    rag_evidence="연방준비제도 경제 데이터 기반 권장사항"
                ),
                Quest(
                    title="응급자금 만들기",
                    if_condition="매월 급여일 다음날",
                    then_action="월 수입의 10%를 별도 적금에 자동이체 설정",
                    estimated_time="15분",
                    difficulty="어려움",
                    success_metric="6개월 분 생활비 적립 달성",
                    rag_evidence="SEC 젊은 성인 재정계획 가이드라인"
                )
            ],
            "skincare": [
                Quest(
                    title="기본 세안 루틴 정착",
                    if_condition="아침과 저녁 세안 시",
                    then_action="순한 세안제로 미지근한 물에 30초간 부드럽게 세안",
                    estimated_time="3분",
                    difficulty="쉬움", 
                    success_metric="2주간 하루도 빠짐없이 실행",
                    rag_evidence="미국피부과학회 기본 세안 권고사항"
                ),
                Quest(
                    title="살리실산 제품 도입",
                    if_condition="저녁 세안 후 주 2회",
                    then_action="0.5% 살리실산 제품을 문제 부위에만 점 발라 적응시키기",
                    estimated_time="5분",
                    difficulty="보통",
                    success_metric="4주간 부작용 없이 사용",
                    rag_evidence="FDA 승인 여드름 치료 성분 가이드라인"
                ),
                Quest(
                    title="종합 여드름 관리 루틴",
                    if_condition="매일 아침, 저녁",
                    then_action="세안 → 토너 → 살리실산 제품 → 보습제 순서로 완전한 루틴",
                    estimated_time="10분",
                    difficulty="어려움",
                    success_metric="3개월 연속 피부 상태 개선",
                    rag_evidence="피부과 전문의 협업 종합 관리 프로토콜"
                )
            ]
        }
        
        return quest_templates.get(category, [
            Quest(
                title="기본 실천하기",
                if_condition="적절한 시간에",
                then_action="관련 기본 행동 실행",
                estimated_time="5분",
                difficulty="쉬움",
                success_metric="꾸준한 실행",
                rag_evidence="전문가 기본 권고사항"
            )
        ])

# 메인 실행 함수
async def main():
    """메인 테스트 함수"""
    
    print("🎯 Smart Quest System 테스트 시작")
    print("="*60)
    
    # 시스템 초기화
    system = SmartQuestSystem()
    
    if not system.system_ready:
        print("❌ 시스템 초기화 실패 - 종료합니다.")
        return
    
    print("\n✅ 시스템이 성공적으로 초기화되었습니다!")
    print("="*60)
    print("사용자 피드백을 입력하세요 (종료: 'quit')")
    print()
    print("💡 테스트 예시:")
    print("   목이 아파서 집에서 할 수 있는 쉬운 운동 알려줘")
    print("   20대 직장인 예산 관리 방법")
    print("   여드름 치료 방법 알려줘")
    print()
    
    while True:
        try:
            user_input = input("📝 피드백 입력: ").strip()
            
            if user_input.lower() in ['quit', 'exit', 'q']:
                print("👋 시스템을 종료합니다.")
                break
            
            if not user_input:
                print("❌ 피드백을 입력해주세요.")
                continue
            
            # 사용자 피드백 생성
            feedback = UserFeedback(
                content=user_input,
                timestamp=datetime.now().isoformat(),
                user_id="test_user"
            )
            
            # 피드백 처리
            result = await system.process_user_feedback(feedback)
            
            # 결과 출력
            if result["success"]:
                print(f"\n🎉 처리 완료!")
                print(f"   📊 처리 방법: {result['method']}")
                
                if 'documents_used' in result:
                    print(f"   📚 활용 문서: {result['documents_used']}개")
                elif 'document_id' in result:
                    print(f"   📝 생성된 문서 ID: {result['document_id']}")
                
                print(f"\n🎮 생성된 퀘스트:")
                for i, quest in enumerate(result['quests'], 1):
                    if isinstance(quest, Quest):
                        print(f"\n   📋 퀘스트 {i}: {quest.title}")
                        print(f"      🕐 언제: {quest.if_condition}")
                        print(f"      ⚡ 행동: {quest.then_action}")
                        print(f"      ⏰ 시간: {quest.estimated_time}")
                        print(f"      📈 난이도: {quest.difficulty}")
                        print(f"      🎯 성공 지표: {quest.success_metric}")
                    else:
                        print(f"   {i}. {quest}")
                        
                # 메타데이터 출력
                if 'metadata' in result:
                    meta = result['metadata']
                    print(f"\n📊 처리 정보:")
                    print(f"   ⏰ 처리 시간: {meta.get('processing_time', 'Unknown')}")
                    if 'similarity_scores' in meta:
                        scores = meta['similarity_scores']
                        print(f"   🔍 유사도 점수: {[f'{s:.3f}' for s in scores]}")
                    if 'trusted_sources_count' in meta:
                        print(f"   🌐 신뢰 소스: {meta['trusted_sources_count']}개")
                        
            else:
                print(f"\n❌ 처리 실패: {result['error']}")
                
                if 'fallback_quest' in result:
                    print(f"   🔄 폴백 퀘스트:")
                    fallback = result['fallback_quest']
                    if isinstance(fallback, list) and fallback:
                        quest = fallback[0]
                        if isinstance(quest, Quest):
                            print(f"      📋 {quest.title}")
                            print(f"      ⚡ {quest.then_action}")
            
            print("\n" + "="*60)
            
        except KeyboardInterrupt:
            print("\n👋 시스템을 종료합니다.")
            break
        except Exception as e:
            print(f"\n❌ 오류 발생: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())