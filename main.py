import base64
import os
import os.path
import os.path
import pickle
import re
from datetime import datetime
from email.mime.text import MIMEText
from typing import Literal, Optional, Dict, List

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

load_dotenv()

os.environ["OPENAI_API_KEY"] = os.getenv("OPENAI_API_KEY")


def get_gmail_service():
    SCOPES = [
        'https://www.googleapis.com/auth/gmail.readonly',
        'https://www.googleapis.com/auth/gmail.compose'
    ]
    creds = None

    if os.path.exists('token.pickle'):
        with open('token.pickle', 'rb') as token:
            creds = pickle.load(token)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                'credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)

        with open('token.pickle', 'wb') as token:
            pickle.dump(creds, token)

    service = build('gmail', 'v1', credentials=creds)
    return service


def list_messages(service, user_id='me', query=''):
    try:
        response = service.users().messages().list(
            userId=user_id, q=query).execute()
        messages = []

        if 'messages' in response:
            messages.extend(response['messages'])

        while 'nextPageToken' in response:
            page_token = response['nextPageToken']
            response = service.users().messages().list(
                userId=user_id, q=query, pageToken=page_token
            ).execute()
            messages.extend(response['messages'])

        return messages
    except Exception as e:
        print(f'An error occurred: {e}')
        return []


def get_message_content(service, message_id, user_id='me'):
    try:
        message = service.users().messages().get(
            userId=user_id, id=message_id, format='full').execute()

        headers = message['payload']['headers']
        subject = next(h['value'] for h in headers if h['name'] == 'Subject')
        sender = next(h['value'] for h in headers if h['name'] == 'From')

        import html
        snippet = html.unescape(message['snippet'])

        return {
            'id': message['id'],
            'subject': subject,
            'sender': sender,
            'snippet': snippet
        }
    except Exception as e:
        print(f'An error occurred: {e}')
        return None


class MailCategory(BaseModel):
    category: Literal[
        "order_status", "product_return", "stock_inquiry", "other", "product_complaint"
    ] = Field(
        description="Category of the email, can be ")


def classify_email(email_content: str) -> MailCategory:
    llm = ChatOpenAI(model="gpt-4", temperature=0).with_structured_output(MailCategory)
    return llm.invoke(email_content)


class Product(BaseModel):
    id: str
    name: str
    price: float
    stock: int
    category: str


class Order(BaseModel):
    id: str
    customer_email: str
    status: str
    items: List[str]
    order_date: datetime
    tracking_number: Optional[str]


class MockDatabase:
    def __init__(self):
        self.products: Dict[str, Product] = {
            "NIKE001": Product(
                id="NIKE001",
                name="Nike Air Zoom Pro",
                price=129.99,
                stock=15,
                category="running_shoes"
            ),
            "ADIDAS001": Product(
                id="ADIDAS001",
                name="Adidas Champions League Ball 2024",
                price=149.99,
                stock=0,
                category="football"
            ),
            "YOGA001": Product(
                id="YOGA001",
                name="Premium Yoga Mat",
                price=49.99,
                stock=23,
                category="yoga"
            )
        }

        self.orders: Dict[str, Order] = {
            "ORD54321": Order(
                id="ORD54321",
                customer_email="margaret.brown@email.com",
                status="processing",
                items=["YOGA001"],
                order_date=datetime(2024, 12, 28),
                tracking_number=None
            ),
            "ORD54322": Order(
                id="ORD54322",
                customer_email="anna.smith@email.com",
                status="shipped",
                items=["NIKE001"],
                order_date=datetime(2024, 12, 20),
                tracking_number="TRK123456789"
            )
        }

    def check_stock(self, product_id: str) -> Optional[Product]:
        return self.products.get(product_id)

    def get_order_status(self, order_id: str) -> Optional[Order]:
        return self.orders.get(order_id)

    def search_orders_by_email(self, email: str) -> List[Order]:
        return [order for order in self.orders.values() if order.customer_email == email]


class EmailResponseGenerator:
    def __init__(self, db: MockDatabase):
        self.db = db
        self.llm = ChatOpenAI(model="gpt-4", temperature=0.7)

    def generate_response(self, category: str, email_content: dict) -> str:
        context = self._get_context(category)

        print(f"Generating response for category: {category}")
        print(f"Using context: {context}")

        prompt = f"""
        You are a customer service representative for a sports equipment store.
        Please generate a professional and helpful response to the customer email below.
        Use the provided context information to give accurate details.

        Original Email:
        Subject: {email_content.get('subject', 'No subject')}
        Content: {email_content.get('snippet', 'No content')}

        Additional Context:
        {context}

        Generate a polite and informative response:
        """

        response = self.llm.invoke(prompt)
        response_text = str(response.content)
        print(f"Generated response: {response_text}")
        return response_text

    def _get_context(self, category: str) -> str:
        if category == "order_status":
            order_id = "ORD54321"
            order = self.db.get_order_status(order_id)
            if order:
                return f"Order {order_id} is {order.status}. Tracking number: {order.tracking_number or 'Not yet available'}"

        elif category == "stock_inquiry":
            product = self.db.check_stock("ADIDAS001")
            if product:
                return f"Product {product.name} stock level: {product.stock}"

        return "No additional context available"


def create_draft_email(service, to: str, subject: str, message_body: str):
    try:
        email_pattern = re.compile(r'"?([^"]*)"?\s*<(.+?)>')
        match = email_pattern.search(to)

        if match:
            clean_email = match.group(2)
        else:
            clean_email = to.strip()

        print(f"Extracted email: {clean_email}")

        message = MIMEText(message_body)
        message['to'] = clean_email
        message['subject'] = subject
        message['from'] = "me"

        raw_message = base64.urlsafe_b64encode(
            message.as_bytes()
        ).decode('utf-8')

        draft = service.users().drafts().create(
            userId='me',
            body={'message': {'raw': raw_message}}
        ).execute()

        print(f"Successfully created draft to: {clean_email}")
        return draft

    except Exception as e:
        print(f'An error occurred in create_draft_email: {e}')
        print(f'To address was: {to}')
        return None


if __name__ == '__main__':
    db = MockDatabase()
    response_generator = EmailResponseGenerator(db)

    service = get_gmail_service()

    messages = list_messages(service, query='after:2024/12/22')

    for email in messages:
        print(f"Processing email ID: {email['id']}")

        content = get_message_content(service, email["id"])
        if content is None:
            print(f"Could not fetch content for email ID: {email['id']}")
            continue

        print(f"Retrieved content: {content}")

        mail_category = classify_email(content['snippet'])
        print(f"Classified as: {mail_category.category}")

        if mail_category.category in ["other"]:
            print(f"Category not supported for email: {content['subject']}")
            continue

        response = response_generator.generate_response(
            mail_category.category,
            content
        )

        draft = create_draft_email(
            service,
            content['sender'],
            f"Re: {content['subject']}",
            response
        )

        if draft:
            print(f"Draft created for email: {content['subject']}")
            print(f"Response: {response}\n")
