"""
SOFIA - Camera Finder
----------------------
Lists available camera indices and shows a live preview of each one for a
few seconds, labeled with its index, so you can identify which index
corresponds to your laptop's built-in webcam.

Press 'n' to skip to the next camera early, 'q' to quit entirely.
"""

import cv2

MAX_INDEX_TO_CHECK = 6
PREVIEW_SECONDS = 4


def main():
    found = []

    for idx in range(MAX_INDEX_TO_CHECK):
        cap = cv2.VideoCapture(idx)
        if not cap.isOpened():
            cap.release()
            continue

        ok, frame = cap.read()
        if not ok or frame is None:
            cap.release()
            continue

        found.append(idx)
        print(f"Camera index {idx}: OK ({frame.shape[1]}x{frame.shape[0]})")

        start = cv2.getTickCount()
        freq = cv2.getTickFrequency()
        while True:
            ok, frame = cap.read()
            if not ok:
                break

            cv2.putText(
                frame, f"CAMERA INDEX {idx}  (press 'n' for next)",
                (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2,
            )
            cv2.imshow("SOFIA - Camera Finder", frame)

            key = cv2.waitKey(1) & 0xFF
            elapsed = (cv2.getTickCount() - start) / freq
            if key == ord("n") or elapsed > PREVIEW_SECONDS:
                break
            if key == ord("q"):
                cap.release()
                cv2.destroyAllWindows()
                print(f"\nWorking camera indices found: {found}")
                return

        cap.release()

    cv2.destroyAllWindows()
    print(f"\nWorking camera indices found: {found}")
    print("Set CAM_INDEX in sofia_hand_tracker.py to the one that showed your laptop's webcam.")


if __name__ == "__main__":
    main()
