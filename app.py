from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
import firebase_admin
from firebase_admin import credentials, firestore, storage
import requests
from requests.auth import HTTPBasicAuth
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

app = Flask(__name__)

# Initialize Firebase
cred_path = os.getenv('FIREBASE_CREDENTIALS_PATH')
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
    incoming_msg = request.values.get('Body', '').lower()
    num_media = int(request.values.get('NumMedia', 0))
    from_number = request.values.get('From', '')

    resp = MessagingResponse()
    msg = resp.message()

    state = user_state.get(from_number, 'greeting')

    if state == 'greeting':
        msg.body("Hello! What would you like to do?\n\n" +
                 "*Send a Document:* Reply with 'Send'\n" +
                 "*Receive a Document:* Reply with 'Receive'\n" +
                 "*End the chat:* Reply with 'End'")
        user_state[from_number] = 'waiting_for_action'

    elif state == 'waiting_for_action':
        if 'send' in incoming_msg:
            msg.body("Which document do you want to send?\n\n" +
                     "*Aadhar*\n" +
                     "*PAN*\n" +
                     "*Driving License*")
            user_state[from_number] = 'waiting_for_document_type'
        elif 'receive' in incoming_msg:
            msg.body("Which document do you want to receive?\n\n" +
                     "*Aadhar*\n" +
                     "*PAN*\n" +
                     "*Driving License*")
            user_state[from_number] = 'waiting_for_receive_document_type'
        elif 'end' in incoming_msg:
            msg.body("Chat ended. You can start over by sending any message.")
            user_state.pop(from_number, None)
        else:
            msg.body("Please choose an option:\n" +
                     "*Send a Document*\n" +
                     "*Receive a Document*\n" +
                     "*End the chat*")

    elif state == 'waiting_for_document_type':
        if incoming_msg in ['aadhar', 'pan', 'driving license']:
            user_state[from_number] = {'state': 'waiting_for_document', 'doc_type': incoming_msg}
            msg.body(f"Please send the {incoming_msg} document now.")
        elif 'end' in incoming_msg:
            msg.body("Chat ended. You can start over by sending any message.")
            user_state.pop(from_number, None)
        else:
            msg.body("Invalid document type. Please choose again:\n" +
                     "*Aadhar*\n" +
                     "*PAN*\n" +
                     "*Driving License*")

    elif state == 'waiting_for_receive_document_type':
        if incoming_msg in ['aadhar', 'pan', 'driving license']:
            documents = db.collection('documents').where('type', '==', incoming_msg.capitalize()).where('user', '==', from_number).stream()
            document_found = False
            for doc in documents:
                document = doc.to_dict()
                msg.body(f"Here is your {incoming_msg} document: {document['url']}")
                document_found = True
                break
            if not document_found:
                msg.body(f"No {incoming_msg} document found.")
            user_state.pop(from_number, None)
        elif 'end' in incoming_msg:
            msg.body("Chat ended. You can start over by sending any message.")
            user_state.pop(from_number, None)
        else:
            msg.body("Invalid document type. Please choose again:\n" +
                     "*Aadhar*\n" +
                     "*PAN*\n" +
                     "*Driving License*")

    elif isinstance(state, dict) and state.get('state') == 'waiting_for_document':
        if num_media > 0:
            media_url = request.values['MediaUrl0']
            media_content_type = request.values['MediaContentType0']
            media_extension = media_content_type.split('/')[-1]
            media_sid = request.values['MessageSid']
            doc_type = state['doc_type']

            account_sid = os.getenv('TWILIO_ACCOUNT_SID')
            auth_token = os.getenv('TWILIO_AUTH_TOKEN')
            auth = HTTPBasicAuth(account_sid, auth_token)
            media_data = download_media(media_url, auth)

            if media_data:
                filename = f"{doc_type}_{media_sid}.{media_extension}"
                blob = bucket.blob(f"documents/{filename}")
                blob.upload_from_string(media_data, content_type=media_content_type)
                blob.make_public()

                db.collection('documents').add({
                    'filename': filename,
                    'content_type': media_content_type,
                    'url': blob.public_url,
                    'type': doc_type.capitalize(),
                    'user': from_number
                })
                msg.body(f"{doc_type.capitalize()} document received and saved.")
                user_state.pop(from_number, None)
            else:
                msg.body("Failed to download media.")
        elif 'end' in incoming_msg:
            msg.body("Chat ended. You can start over by sending any message.")
            user_state.pop(from_number, None)
        else:
            msg.body("Please send a document as media. To end the chat, reply with 'end'.")

    else:
        msg.body("An error occurred. Please start over.")

    return str(resp)

@app.errorhandler(404)
def page_not_found(e):
    return "Page not found", 404

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 5000)))

