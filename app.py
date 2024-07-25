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
    incoming_msg = request.values.get('Body', '').lower().strip()
    num_media = int(request.values.get('NumMedia', 0))
    from_number = request.values.get('From', '')

    resp = MessagingResponse()
    msg = resp.message()

    state = user_state.get(from_number, 'greeting')

    if state == 'greeting':
        msg.body("Hello! What would you like to do?\n\n" +
                 "1. *Send a Document*\n" +
                 "2. *Receive a Document*\n" +
                 "3. *End the chat*")
        user_state[from_number] = 'waiting_for_action'

    elif state == 'waiting_for_action':
        if incoming_msg == '1':
            msg.body("Which document do you want to send?\n\n" +
                     "1. *Aadhar*\n" +
                     "2. *PAN*\n" +
                     "3. *Driving License*")
            user_state[from_number] = 'waiting_for_document_type'
        elif incoming_msg == '2':
            msg.body("Which document do you want to receive?\n\n" +
                     "1. *Aadhar*\n" +
                     "2. *PAN*\n" +
                     "3. *Driving License*")
            user_state[from_number] = 'waiting_for_receive_document_type'
        elif incoming_msg == '3':
            msg.body("Chat ended. You can start over by sending any message.")
            user_state.pop(from_number, None)
        else:
            msg.body("Please choose an option:\n" +
                     "1. *Send a Document*\n" +
                     "2. *Receive a Document*\n" +
                     "3. *End the chat*")

    elif state == 'waiting_for_document_type':
        doc_types = {'1': 'aadhar', '2': 'pan', '3': 'driving license'}
        doc_type = doc_types.get(incoming_msg)
        if doc_type:
            user_state[from_number] = {'state': 'waiting_for_document', 'doc_type': doc_type}
            msg.body(f"Please send the {doc_type} document now.")
        elif incoming_msg == '3':
            msg.body("Chat ended. You can start over by sending any message.")
            user_state.pop(from_number, None)
        else:
            msg.body("Invalid option. Please choose again:\n" +
                     "1. *Aadhar*\n" +
                     "2. *PAN*\n" +
                     "3. *Driving License*")

    elif state == 'waiting_for_receive_document_type':
        doc_types = {'1': 'aadhar', '2': 'pan', '3': 'driving license'}
        doc_type = doc_types.get(incoming_msg)
        if doc_type:
            documents = db.collection('documents').where('type', '==', doc_type.capitalize()).where('user', '==', from_number).stream()
            document_found = False
            for doc in documents:
                document = doc.to_dict()
                msg.body(f"Here is your {doc_type} document: {document['url']}")
                document_found = True
                break
            if not document_found:
                msg.body(f"No {doc_type} document found.")
            user_state.pop(from_number, None)
        elif incoming_msg == '3':
            msg.body("Chat ended. You can start over by sending any message.")
            user_state.pop(from_number, None)
        else:
            msg.body("Invalid option. Please choose again:\n" +
                     "1. *Aadhar*\n" +
                     "2. *PAN*\n" +
                     "3. *Driving License*")

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
        elif incoming_msg == '3':
            msg.body("Chat ended. You can start over by sending any message.")
            user_state.pop(from_number, None)
        else:
            msg.body("Please send a document as media. To end the chat, reply with '3'.")

    else:
        msg.body("An error occurred. Please start over.")

    return str(resp)

@app.errorhandler(404)
def page_not_found(e):
    return "Page not found", 404

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 5000)))
