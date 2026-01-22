import os
import time
import random
import threading
import requests
import uuid
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# Global dictionary to store task states
# Structure: { task_id: {'running': bool, 'thread': Thread_Object} }
running_tasks = {}
UPLOAD_FOLDER = 'uploads'
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

def send_message(token, thread_id, text=None, image_path=None):
    """
    Sends a message to the Facebook thread.
    """
    url = f"https://graph.facebook.com/v15.0/t_{thread_id}/"
    params = {"access_token": token.strip()}

    # -------------------------
    # SEND TEXT
    # -------------------------
    if text:
        # Escape double quotes for JSON compatibility
        raw_text = f'{{"text": "{text.replace('"', '\\"')}"}}'
        try:
            res = requests.post(
                url,
                params=params,
                data=raw_text,
                headers={"Content-Type": "application/json"}
            )
            print(f"Text Response: {res.status_code} - {res.text}")
        except Exception as e:
            print(f"Text Send Error: {e}")

    # -------------------------
    # SEND IMAGE
    # -------------------------
    if image_path and os.path.exists(image_path):
        try:
            # Prepare the attachment payload
            raw_attach = '{"attachment":{"type":"image","payload":{}}}'
            
            with open(image_path, "rb") as img_file:
                files = {
                    "filedata": (
                        os.path.basename(image_path),
                        img_file,
                        "image/jpeg"
                    )
                }

                res2 = requests.post(
                    url,
                    params=params,
                    data={"message": raw_attach},
                    files=files
                )
                print(f"Image Response: {res2.status_code} - {res2.text}")
        except Exception as e:
            print(f"Image Send Error: {e}")

    return "OK"


def background_task(task_id, tokens, thread_id, prefix, interval, messages, images):
    """
    The core loop that runs in the background thread.
    """
    idx_msg = 0
    idx_img = 0
    
    # Ensure lists are not None to avoid errors
    messages = messages or []
    images = images or []

    print(f"[{task_id}] Background task started.")

    while running_tasks.get(task_id, {}).get('running', False):
        try:
            # Select random token
            token = random.choice(tokens).strip()
            if not token:
                continue

            # Prepare message
            msg = messages[idx_msg] if messages else ""
            final_msg = f"{prefix} {msg}".strip()
            
            # Prepare image
            img = images[idx_img] if images else None

            # Send
            send_message(token, thread_id, final_msg, img)

            # Update indices
            if messages:
                idx_msg = (idx_msg + 1) % len(messages)
            if images:
                idx_img = (idx_img + 1) % len(images)

            # Sleep with random jitter
            sleep_time = interval + random.randint(5, 15)
            time.sleep(sleep_time)

        except Exception as e:
            print(f"[{task_id}] Error in loop: {e}")
            time.sleep(10) # Wait before retrying on error

    print(f"[{task_id}] Task stopped.")
    
    # Optional: Cleanup images after task stops
    # for img_path in images:
    #     try:
    #         if os.path.exists(img_path):
    #             os.remove(img_path)
    #     except:
    #         pass


# =====================================================
# API ENDPOINTS
# =====================================================

@app.route("/start", methods=["POST"])
def start():
    """
    Expects JSON payload or Form data.
    Files: 'messages' (text file), 'images' (multiple files)
    """
    # Check if task_id already exists and is running
    task_id = request.form.get("task_id") or request.json.get("task_id") if request.is_json else None
    
    if not task_id:
        return jsonify({"status": "error", "message": "task_id is required"}), 400

    if running_tasks.get(task_id, {}).get('running', False):
        return jsonify({"status": "error", "message": "Task is already running"}), 400

    # Parse Data
    if request.is_json:
        data = request.json
        tokens = data.get("tokens", [])
        thread_id = data.get("thread_id")
        prefix = data.get("prefix", "")
        interval = int(data.get("interval", 60))
        msg_list = data.get("messages", []) # Expecting list of strings
        img_paths = []
    else:
        # Form Data
        tokens = request.form.get("tokens", "").split("\n")
        thread_id = request.form.get("thread_id")
        prefix = request.form.get("prefix", "")
        interval = int(request.form.get("interval", 60))
        
        # Handle Text File Upload
        msg_file = request.files.get("messages")
        msg_list = []
        if msg_file:
            content = msg_file.read().decode('utf-8', errors='ignore').split("\n")
            msg_list = [x.strip() for x in content if x.strip()]

        # Handle Image Uploads
        img_files = request.files.getlist("images")
        img_paths = []
        for img in img_files:
            if img.filename: # Check if file actually selected
                # Generate unique filename to prevent overwriting
                unique_filename = f"{uuid.uuid4()}_{img.filename}"
                save_path = os.path.join(UPLOAD_FOLDER, unique_filename)
                img.save(save_path)
                img_paths.append(save_path)

    # Validate required fields
    if not tokens or not thread_id:
        return jsonify({"status": "error", "message": "tokens and thread_id are required"}), 400

    # Start Thread
    running_tasks[task_id] = {'running': True, 'thread': None}
    
    thread = threading.Thread(
        target=background_task,
        args=(task_id, tokens, thread_id, prefix, interval, msg_list, img_paths)
    )
    thread.daemon = True # Ensures thread dies when main app exits
    running_tasks[task_id]['thread'] = thread
    thread.start()

    return jsonify({
        "status": "Task Started",
        "task_id": task_id,
        "interval": interval,
        "messages_count": len(msg_list),
        "images_count": len(img_paths)
    })


@app.route("/stop", methods=["POST"])
def stop():
    data = request.get_json() or request.form
    task_id = data.get("task_id")

    if not task_id:
        return jsonify({"status": "error", "message": "task_id is required"}), 400

    if task_id in running_tasks:
        running_tasks[task_id]['running'] = False
        return jsonify({"status": "Task Stopping", "task_id": task_id})
    else:
        return jsonify({"status": "error", "message": "Task not found"}), 404


@app.route("/status", methods=["GET"])
def status():
    """
    Returns the status of all tasks or a specific task.
    Usage: /status?task_id=your_id
    """
    task_id = request.args.get("task_id")
    
    if task_id:
        is_running = running_tasks.get(task_id, {}).get('running', False)
        return jsonify({"task_id": task_id, "running": is_running})
    
    # Return all tasks
    all_tasks = {tid: info['running'] for tid, info in running_tasks.items()}
    return jsonify({"tasks": all_tasks})


@app.route("/")
def index():
    return send_from_directory("", "index.html")


# =====================================================
# MAIN
# =====================================================
if __name__ == "__main__":
    # Create uploads directory if not exists
    if not os.path.exists(UPLOAD_FOLDER):
        os.makedirs(UPLOAD_FOLDER)
    
    print("Starting Server on http://0.0.0.0:5000")
    app.run(host="0.0.0.0", port=5000, debug=True)
