from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
import firebase_admin
from firebase_admin import credentials, firestore, storage
import requests
from requests.auth import HTTPBasicAuth
import os
from dotenv import load_dotenv
import logging
import time

# Load environment variables
load_dotenv()

app = Flask(__name__)

# Set up logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Initialize Firebase
cred_path = os.getenv('FIREBASE_CREDENTIALS_PATH')
if cred_path is None:
    raise ValueError("FIREBASE_CREDENTIALS_PATH environment variable is not set")

cred = credentials.Certificate(cred_path)
firebase_admin.initialize_app(cred, {
    'storageBucket': os.getenv('FIREBASE_STORAGE_BUCKET')
})

# Initialize Firestore and Storage
db = firestore.client()
bucket = storage.bucket()

user_state = {}

def download_media(media_url, auth):
    response = requests.get(media_url, auth=auth)
    return response.content if response.status_code == 200 else None

@app.route("/whatsapp", methods=['POST'])
def whatsapp():
    start_time = time.time()
    incoming_msg = request.values.get('Body', '').lower()
    num_media = int(request.values.get('NumMedia', 0))
    from_number = request.values.get('From', '')
    logger.debug(f"Incoming message from {from_number}: {incoming_msg}")
    logger.debug(f"Number of media: {num_media}")

    resp = MessagingResponse()
    msg = resp.message()

    state = user_state.get(from_number, 'greeting')
    logger.debug(f"Current state for {from_number}: {state}")

    try:
        if state == 'greeting':
            response_message = "Hello! Do you want to send or receive a document? Please reply with 'send' or 'receive'. To end the chat, reply with 'end'."
            msg.body(response_message)
            user_state[from_number] = 'waiting_for_action'
            logger.debug(f"State updated to 'waiting_for_action' for {from_number}")
        
        elif state == 'waiting_for_action':
            if 'send' in incoming_msg:
                response_message = "Which document do you want to send? Please reply with 'Aadhar', 'PAN', or 'Driving License'."
                msg.body(response_message)
                user_state[from_number] = 'waiting_for_document_type'
            elif 'receive' in incoming_msg:
                response_message = "Which document do you want to receive? Please reply with 'Aadhar', 'PAN', or 'Driving License'."
                msg.body(response_message)
                user_state[from_number] = 'waiting_for_receive_document_type'
            elif 'end' in incoming_msg:
                response_message = "Chat ended. You can start over by sending any message."
                msg.body(response_message)
                user_state.pop(from_number, None)
            else:
                response_message = "Please reply with 'send', 'receive', or 'end' to proceed."
                msg.body(response_message)
        
        elif state == 'waiting_for_document_type':
            if incoming_msg in ['aadhar', 'pan', 'driving license']:
                user_state[from_number] = {'state': 'waiting_for_document', 'doc_type': incoming_msg}
                response_message = f"Please send the {incoming_msg} document now."
                msg.body(response_message)
            elif 'end' in incoming_msg:
                response_message = "Chat ended. You can start over by sending any message."
                msg.body(response_message)
                user_state.pop(from_number, None)
            else:
                response_message = "Invalid document type. Please reply with 'Aadhar', 'PAN', or 'Driving License'. To end the chat, reply with 'end'."
                msg.body(response_message)
        
        elif state == 'waiting_for_receive_document_type':
            if incoming_msg in ['aadhar', 'pan', 'driving license']:
                documents = db.collection('documents').where('type', '==', incoming_msg.capitalize()).stream()
                document_found = False
                for doc in documents:
                    document = doc.to_dict()
                    logger.debug(f"Found document: {document}")
                    response_message = f"Here is your {incoming_msg} document: {document['url']}"
                    msg.body(response_message)
                    document_found = True
                    break
                if not document_found:
                    response_message = f"No {incoming_msg} document found."
                    msg.body(response_message)
                user_state.pop(from_number, None)
            elif 'end' in incoming_msg:
                response_message = "Chat ended. You can start over by sending any message."
                msg.body(response_message)
                user_state.pop(from_number, None)
            else:
                response_message = "Invalid document type. Please reply with 'Aadhar', 'PAN', or 'Driving License'. To end the chat, reply with 'end'."
                msg.body(response_message)
        
        elif isinstance(state, dict) and state.get('state') == 'waiting_for_document':
            if num_media > 0:
                media_url = request.values['MediaUrl0']
                media_content_type = request.values['MediaContentType0']
                media_extension = media_content_type.split('/')[-1]
                media_sid = request.values['MessageSid']
                doc_type = state['doc_type']

                logger.debug(f"Downloading media from URL: {media_url}")

                # Add authentication to download the media
                account_sid = os.getenv('TWILIO_ACCOUNT_SID')
                auth_token = os.getenv('TWILIO_AUTH_TOKEN')
                auth = HTTPBasicAuth(account_sid, auth_token)
                media_data = download_media(media_url, auth)

                if media_data:
                    # Upload to Firebase Storage
                    filename = f"{doc_type}_{media_sid}.{media_extension}"
                    blob = bucket.blob(f"documents/{filename}")
                    blob.upload_from_string(media_data, content_type=media_content_type)
                    logger.debug(f"File uploaded to Firebase Storage: {blob.name}")

                    # Make the blob public
                    blob.make_public()
                    logger.debug(f"File made public: {blob.public_url}")

                    # Set and update metadata
                    metadata = {
                        'contentType': media_content_type,
                        'metadata': {
                            'filename': filename,
                            'description': f'{doc_type} document uploaded from WhatsApp'
                        }
                    }
                    blob.metadata = metadata
                    blob.patch()
                    logger.debug(f"Metadata set for blob: {blob.metadata}")

                    # Store metadata in Firestore
                    try:
                        doc_ref = db.collection('documents').add({
                            'filename': filename,
                            'content_type': media_content_type,
                            'url': blob.public_url,
                            'type': doc_type.capitalize()
                        })
                        logger.debug(f"Document metadata stored in Firestore with ID: {doc_ref.id}")
                    except Exception as e:
                        logger.error(f"Error storing metadata in Firestore: {e}")
                        response_message = "Failed to store document metadata."
                        msg.body(response_message)
                        return str(resp)

                    response_message = f"{doc_type.capitalize()} document received and saved."
                    msg.body(response_message)
                    # Clear user state
                    user_state.pop(from_number, None)
                else:
                    response_message = "Failed to download media."
                    msg.body(response_message)
                    logger.error(f"Failed to download media from {media_url}")
            elif 'end' in incoming_msg:
                response_message = "Chat ended. You can start over by sending any message."
                msg.body(response_message)
                user_state.pop(from_number, None)
            else:
                response_message = "Please send a document as media. To end the chat, reply with 'end'."
                msg.body(response_message)
        
        else:
            response_message = "An error occurred. Please start over."
            msg.body(response_message)
        
        logger.debug(f"Response message: {response_message}")
        logger.debug(f"Response time: {time.time() - start_time} seconds")
        return str(resp)
    except Exception as e:
        logger.error(f"Error handling message: {e}")
        msg.body("An error occurred while processing your request.")
        return str(resp)

@app.route("/status", methods=['POST'])
def status():
    return "Status endpoint"

@app.errorhandler(404)
def page_not_found(e):
    logger.error(f"404 Error: {e}")
    return "Page not found", 404

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 5000)))

