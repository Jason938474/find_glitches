#!/usr/bin/env python3
"""
clean_video.py / find_glitches.py

Detects and repairs single-frame and dual-frame video corruption bursts using
structural edge-difference tracking and rolling lookahead queues.
Includes manual JSON override support and bad-frame auditing exports.
"""

import argparse
import json
import os
import shutil
import subprocess
import cv2
import numpy as np


def parse_args():
    parser = argparse.ArgumentParser(
        description="Clean video glitches with burst-aware lookahead."
    )
    parser.add_argument("input", help="Path to input video file")
    parser.add_argument(
        "-o", "--output", default="cleaned_output.mp4", help="Path to output video file"
    )
    parser.add_argument(
        "-t",
        "--threshold",
        type=float,
        default=0.025,
        help="Detection threshold (default: 0.025)",
    )
    parser.add_argument(
        "-w",
        "--window",
        type=int,
        default=2,
        help="Maximum burst lookahead window (default: 2)",
    )
    parser.add_argument(
        "-c",
        "--config",
        default="manual_frames.json",
        help="Path to manual override JSON",
    )
    parser.add_argument(
        "-a",
        "--audit-dir",
        default="bad-frames",
        help="Directory to save audit images",
    )
    return parser.parse_args()


def load_manual_overrides(config_path):
    force_repair = set()
    force_keep = set()
    if os.path.exists(config_path):
        try:
            with open(config_path, "r") as f:
                data = json.load(f)
                force_repair = set(data.get("force_repair", []))
                force_keep = set(data.get("force_keep", []))
            print(
                f"Loaded overrides: {len(force_repair)} force_repair, {len(force_keep)} force_keep"
            )
        except Exception as e:
            print(f"Warning: Failed to parse {config_path}: {e}")
    return force_repair, force_keep


def calculate_diff(f1, f2):
    """Calculates normalized mean absolute difference on grayscale blurred frames."""
    g1 = cv2.GaussianBlur(cv2.cvtColor(f1, cv2.COLOR_BGR2GRAY), (5, 5), 0)
    g2 = cv2.GaussianBlur(cv2.cvtColor(f2, cv2.COLOR_BGR2GRAY), (5, 5), 0)
    diff = cv2.absdiff(g1, g2)
    return np.mean(diff) / 255.0


def main():
    args = parse_args()

    if not os.path.exists(args.input):
        print(f"Error: Input video '{args.input}' not found.")
        return

    os.makedirs(args.audit_dir, exist_ok=True)
    force_repair, force_keep = load_manual_overrides(args.config)

    cap = cv2.VideoCapture(args.input)
    if not cap.isOpened():
        print(f"Error: Cannot open video '{args.input}'.")
        return

    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    print(f"Processing  : {args.input}")
    print(f"Resolution  : {width}x{height} | FPS: {fps:.2f} | Frames: {total_frames}")
    print(f"Threshold   : {args.threshold} | Max Burst Window: {args.window}")
    print(f"Audit Dir   : {args.audit_dir}/")

    temp_video = "temp_clean_video.mp4"
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    out = cv2.VideoWriter(temp_video, fourcc, fps, (width, height))

    # Read the first clean reference frame
    ret, first_frame = cap.read()
    if not ret:
        print("Error: Empty video.")
        return

    last_known_good = first_frame.copy()
    out.write(first_frame)

    # Queue contains tuples of: (frame_index, frame_bgr)
    queue = []
    current_read_idx = 1

    # Pre-fill lookahead queue
    queue_target_len = args.window + 2
    for _ in range(queue_target_len):
        ret, f = cap.read()
        if not ret:
            break
        queue.append((current_read_idx, f))
        current_read_idx += 1

    repaired_count = 0
    diff_records = []

    try:
        while queue:
            curr_idx, curr_f = queue[0]

            # Keep feeding queue to maintain lookahead depth
            while len(queue) < queue_target_len and current_read_idx < total_frames:
                ret, f = cap.read()
                if not ret:
                    break
                queue.append((current_read_idx, f))
                current_read_idx += 1

            # --- Check 1: Manual Keeps ---
            if curr_idx in force_keep:
                last_known_good = curr_f.copy()
                out.write(curr_f)
                queue.pop(0)
                continue

            # --- Check 2: Manual Repair ---
            if curr_idx in force_repair:
                print(f"[MANUAL REPAIR] Frame {curr_idx}")
                detected_burst_length = 1
                detected_diff = 999.0
            else:
                # --- Check 3: Automated Detection ---
                d_curr_to_prev = calculate_diff(last_known_good, curr_f)
                diff_records.append(d_curr_to_prev)

                detected_burst_length = 0
                detected_diff = 0.0

                # STEP A: Test single-frame glitch first (prevents bad->good->bad misfires)
                if len(queue) >= 2:
                    next_f = queue[1][1]
                    d_next_to_prev = calculate_diff(last_known_good, next_f)
                    d_curr_to_next = calculate_diff(curr_f, next_f)

                    single_spike = min(d_curr_to_prev, d_curr_to_next) - d_next_to_prev
                    if single_spike > args.threshold:
                        detected_burst_length = 1
                        detected_diff = single_spike

                # STEP B: Test 2-frame burst if single-frame didn't trigger
                if detected_burst_length == 0 and args.window >= 2 and len(queue) >= 3:
                    f1 = curr_f
                    f2 = queue[1][1]
                    anchor = queue[2][1]

                    d_f1_to_prev = d_curr_to_prev
                    d_f2_to_anchor = calculate_diff(f2, anchor)
                    d_anchor_to_prev = calculate_diff(last_known_good, anchor)

                    d_f1_to_anchor = calculate_diff(f1, anchor)
                    d_f2_to_prev = calculate_diff(last_known_good, f2)

                    expected_step = d_anchor_to_prev / 3.0

                    f1_bad = d_f1_to_prev > (expected_step + args.threshold) or d_f1_to_anchor > (expected_step + args.threshold)
                    f2_bad = d_f2_to_anchor > (expected_step + args.threshold) or d_f2_to_prev > (expected_step + args.threshold)
                    anchors_clean = d_anchor_to_prev < (args.threshold * 2.5)

                    if f1_bad and f2_bad and anchors_clean:
                        burst_spike = min(d_f1_to_prev, d_f2_to_anchor) - expected_step
                        if burst_spike > args.threshold:
                            detected_burst_length = 2
                            detected_diff = burst_spike

            # --- Frame Handling / Repair Execution ---
            if detected_burst_length > 0:
                clean_anchor_idx = detected_burst_length
                has_anchor = len(queue) > clean_anchor_idx
                clean_anchor = queue[clean_anchor_idx][1] if has_anchor else last_known_good

                print(
                    f"[DETECTED] Frame {curr_idx} (Burst: {detected_burst_length} frames, Diff: {detected_diff:+.4f})"
                )

                # Process the burst frames
                for step in range(detected_burst_length):
                    b_idx, b_frame = queue.pop(0)

                    # Export audit frame
                    audit_path = os.path.join(
                        args.audit_dir,
                        f"bad_frame_{b_idx:06d}_diff_{detected_diff:.4f}.png",
                    )
                    cv2.imwrite(audit_path, b_frame)

                    # Linear blend across the gap
                    if has_anchor:
                        alpha = (step + 1) / (detected_burst_length + 1)
                        repaired = cv2.addWeighted(
                            last_known_good, 1.0 - alpha, clean_anchor, alpha, 0
                        )
                    else:
                        repaired = last_known_good.copy()

                    out.write(repaired)
                    repaired_count += 1

                # If we have an anchor, it becomes our new known good frame
                if has_anchor:
                    last_known_good = clean_anchor.copy()
            else:
                # Frame is clean: accept it
                last_known_good = curr_f.copy()
                out.write(curr_f)
                queue.pop(0)

            if curr_idx % 500 == 0:
                print(f"Processed frame {curr_idx}/{total_frames}...")

    finally:
        cap.release()
        out.release()

    print("\n" + "=" * 50)
    print(f"Processing Complete.")
    print(f"Total Repaired Frames: {repaired_count}")
    if diff_records:
        print(
            f"Diff Baseline -> Min: {min(diff_records):.4f}, Max: {max(diff_records):.4f}, Avg: {np.mean(diff_records):.4f}"
        )
    print("=" * 50)

    # Remux original audio back in via FFmpeg
    if shutil.which("ffmpeg"):
        print("Muxing original audio stream...")
        ffmpeg_cmd = [
            "ffmpeg",
            "-y",
            "-i",
            temp_video,
            "-i",
            args.input,
            "-c:v",
            "copy",
            "-c:a",
            "copy",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0?",
            "-shortest",
            args.output,
        ]
        subprocess.run(ffmpeg_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if os.path.exists(temp_video):
            os.remove(temp_video)
        print(f"Final output written to -> {args.output}")
    else:
        print("FFmpeg not found. Video only saved as temp_clean_video.mp4")


if __name__ == "__main__":
    main()
