# server.py
import socket
import numpy as np
import cv2
import threading

def call_image(client_socket):
    client_socket.send(b'REQUEST_IMAGE')

def call_distance(client_socket):
    client_socket.send(b'REQUEST_DISTANCE')

def receive_distance(connection):
    # 거리 정보를 받음
    distance_info = connection.recv(16)
    distance = float(distance_info.decode().strip())
    return distance

def receive_frame(connection):
    # 이미지 크기 정보를 먼저 받음
    size_info = connection.recv(16)
    size = int(size_info.decode().strip())
    
    # 해당 크기만큼 데이터 수신
    data = b''
    while len(data) < size:
        packet = connection.recv(4096)
        if not packet:
            break
        data += packet

    # 데이터를 numpy array로 변환
    nparr = np.frombuffer(data, np.uint8)
    frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    return frame

def handle_client(conn):
    try:
        while True:
            command = conn.recv(32)
            if command == b"SENDIMAGE":
                frame = receive_frame(conn)
                if frame is None:
                    break
                # 프레임 크기 조절 (예: 가로 800px, 세로는 비율에 맞게 조절)
                frame_resized = cv2.resize(frame, (800, int(frame.shape[0] * (800 / frame.shape[1]))))
                
                # 프레임 디스플레이
                cv2.imshow('Received Frame', frame_resized)
                if cv2.waitKey(1) & 0xFF == ord('q'):
                    break
            elif command == b"SENDDISTANCE":
                distance = receive_distance(conn)
                print(f"Received distance: {distance} meters")
    except Exception as e:
        print(f"오류 발생: {e}")
    finally:
        conn.close()
        cv2.destroyAllWindows()

def call_func(conn,command):
    if command == "IMAGE":
        call_image(conn)
    elif command == "DISTANCE":
        call_distance(conn)
    else:
        conn.sendall(command.encode('utf-8'))

def init_server():
    # 서버 소켓 설정
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.bind(('0.0.0.0', 5000))
    server_socket.listen(1)
    print("서버 대기 중...")

    conn, addr = server_socket.accept()
    print(f"연결됨: {addr}")
    return conn, addr

def main():
    conn,addr=init_server()
    client_handler = threading.Thread(target=handle_client, args=(conn,))
    client_handler.start()

    while True:
        command = input("Enter command: ").strip().upper()
        call_func(conn,command)

if __name__ == "__main__":
    main()
