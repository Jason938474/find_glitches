# find_glitches
This is a video editing tool I vibe-coded to help restore old somewhat corrupted videos with sequential, small glitches.

The tool reads in the file and examines the delta between the current frame and next.  If the delta is past a certain threshold (specified in either the default or the command-line) it will flag that frame as glitched and move to the next one with a small rolling window of 1-2 frames.  If it finds a frame past that window that matches the last known "good" frame, it will interpolate and fill in the frames between the two found "good" frames for smooth playback.  Once the video restoration is complete, it will pull the audio from the original video and using "ffmpeg" will overlay the audio on the fixed video and create a new complete video.

Any frames it discards will be put into the "bad-frames" directory for examination.  If any of these bad frames actually turn out to be valid, you can keep them using the "manual_frames.json" file under "force_keep" array (sample included).  Each discard will have in it's filename a frame number and the diff value that triggered the exclusion so you can easily do manual included if you wanted. Additionally, you can specify any frames that always need repairing using "force_repair" array.

With the right parameters, I've found even a very glitchy video can be one-shotted perfectly as long as the video doesn't have cuts (e.g. family videos, livestreams, etc) and the glitches are brief. But even with a few cuts, identifying the frame and putting those cuts into the manual_frames.json should allow for everything to continue. The video I was testing against had almost 300 glitches which were fully repaired/restored in one pass.

System Requrements

This was tested in Linux Mint with ffmpeg installed.  You need 2 python files listed in the requirements.txt so at your new location, run the following setup once before your run:

cd install/path
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt

Usage of the script is pretty simple

python find_glitches.py <input-video.mp4>

Optional arguments are:
 
-t <threshold value> - this is the threshold, set a default of 0.025
-c <config file> - manual inclusion of json config file to force skip / repair of frames (default manual_frames.json)
-w <window value> - Number of frames to consider an acceptable glitch window (default 2)
-a <audit dir> - where to put discarded frames, default of ./bad-frames
-o <output file> - alternative output file location


