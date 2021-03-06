"""Script to detect objects that are being shaken in front of the camera."""
import argparse
import math
import sys

import cv2
import numpy

parser = argparse.ArgumentParser(description='Detect/compare objects being shaken in front of the camera.')
parser.add_argument('--source', help='Video to use (default: built-in cam)', default=0)
parser.add_argument('--dump-raw', help='Write raw captured video to this file (default: none)', default=None)
parser.add_argument('--dump-stabilized', help='Write stabilized video to this file (default: none)', default=None)

parser.add_argument('--contours-prefix', help='Write contours to this destination (default: none).', default=None)
parser.add_argument('--objects-prefix', help='Write captured objects to this destination (default: none).', default=None)
parser.add_argument('--masks-prefix', help='Write captured masks (before preprocessing) to this destination (default: none).', default=None)
parser.add_argument('--keep', help='Keep the N objects with the best score and capture them (default: 3).', default=3, type=int)

parser.add_argument('--width', help='Video width (default: 320)', default=320, type=int)
parser.add_argument('--height', help='Video height (default: 200)', default=200, type=int)
parser.add_argument('--blur', help='Blur radius (default: 15)', default=15, type=int)
parser.add_argument('--min-size', help='Assume that everything with fewer pixels is a parasite (default: 100).', default=100, type=int)

parser.add_argument('--buffer', help='Number of frames to capture before proceeding (default: 60).', default=60, type=int)
parser.add_argument('--buffer-stable-frames', help='Number of consecutive *stable* frames to capture before proceeding (default: 0).', default=0, type=int)
parser.add_argument('--buffer-stability', help='Max proportion of the image that can change before we accept that a frame is stable (default: .1)', default=.1, type=float)
parser.add_argument('--buffer-init', help='Proportion of frames to keep for initializing background elimination, must be in ]0, 1[ (default: .9)', default=.9, type=float)

parser.add_argument('--fill', help='Attempt to remove holes from the captured image.', dest='fill_holes', action='store_true')
parser.add_argument('--no-fill', help='Do not attempt to remove holes from the captured image (default).', dest='fill_holes', action='store_false')
parser.set_defaults(fill_holes=False)

parser.add_argument('--autostart', help='Start processing immediately (default).', dest='autostart', action='store_true')
parser.add_argument('--no-autostart', help='Do not start processing immediately.', dest='autostart', action='store_false')
parser.set_defaults(autostart=True)

parser.add_argument('--show', help='Display videos, expect keyboard interaction (default).', dest='show', action='store_true')
parser.add_argument('--no-show', help='Do not display videos, don''t expect keyboard interaction.', dest='show', action='store_false')
parser.set_defaults(show=True)

parser.add_argument('--stabilize', help='Stabilize image (default).', dest='stabilize', action='store_true')
parser.add_argument('--no-stabilize', help='Do not stabilize image.', dest='stabilize', action='store_false')
parser.set_defaults(stabilize=True)

parser.add_argument('--remove-shadows', help='Pixels that look like shadows should not be considered part of the extracted object.', dest='remove_shadows', action='store_true')
parser.add_argument('--no-remove-shadows', help='Pixels that look like shadows should be considered part of the extracted object (default).', dest='remove_shadows', action='store_false')
parser.set_defaults(remove_shadows=False)

parser.add_argument('--use-contour', dest='use_contour', action='store_true')
parser.add_argument('--no-use-contour', dest='use_contour', action='store_false')
parser.set_defaults(use_contour=False)

args = vars(parser.parse_args())
if args['buffer_init'] <= 0:
    args['buffer_init'] = .01
elif args['buffer_init'] >= 1:
    args['buffer_init'] = .99
print ("Args: %s" % args)


def main():
    cap = cv2.VideoCapture(args['source'])
    if cap is None or not cap.isOpened():
        print('Error: unable to open video source')
        return -1

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, args['width'])
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args['height'])

    backgroundSubstractor = cv2.createBackgroundSubtractorKNN()

    idle = True
    force_start = args['autostart']

    # A buffer holding the frames. It will hold up to args['buffer'] framesselfself.
    frames = None

    # The size of the largest suffix of `frames` composed solely of stable frames.
    consecutive_stable_frames = 0
    surface = args['width'] * args['height']

    raw_writer = None
    if args['dump_raw']:
        raw_writer = cv2.VideoWriter(args['dump_raw'], cv2.VideoWriter_fourcc(*"DIVX"), 16, (args['width'], args['height']));

    while(True):
        # Capture frame-by-frame.
        if not cap:
            break
        ret, current = cap.read()

        key = None
        if args['show']:
            key = cv2.waitKey(1) & 0xFF
            # <q> or <Esc>: quit
            if key == 27 or key == ord('q'):
                break
            # <spacebar> or `force_start`: start detecting.
            elif key == ord(' '):
                force_start = True

        if force_start:
            force_start = False
            idle = False
            frames = []
            consecutive_stable_frames = 0

        if not ret:
            print("No more frames.")
            break

        # Display the current frame
        if args['show']:
            cv2.imshow('frame', current)
            cv2.moveWindow('frame', 0, 0)

        if raw_writer:
            raw_writer.write(current)

        if idle:
            # We are not capturing at the moment.
            print("Idle, proceeding.")
            continue

        if not cap.isOpened():
            print("Video source closed.")
            break

        if len(frames) > 0 and args['buffer_stable_frames'] > 0:
            diff = compute_diff(frames[-1], current)
            print("Diff: %d <? %d" % (diff, surface * args['buffer_stability']))
            if diff <= surface * args['buffer_stability']:
                consecutive_stable_frames += 1
            else:
                consecutive_stable_frames = 0

        # We are not done buffering.
        print("Got %d/%d frames, %d/%d stable frames." % (len(frames), args['buffer'], consecutive_stable_frames, args['buffer_stable_frames']))
        frames.append(current)

        if len(frames) >= args['buffer']:
            if consecutive_stable_frames >= args['buffer_stable_frames']:
                # We have enough frames and enough stable frames.
                print("We have enough stable frames.")
                break
            else:
                # Make way for more frames.
                frames.pop(0)
        print("Continuing capture.")

    print("Capture complete.")

    # At this stage, we are done buffering, either because there are no more
    # frames at hand or because we have enough frames. Stop recording, start
    # processing.
    cap.release()
    cap = None


    if args['stabilize']:
        print("Stabilizing.")
        frames = stabilize(frames)

    # Extract foreground
    candidates = []

    print("Removing background.")
    for i, frame in enumerate(frames):
        height, width = frame.shape[:2]

        mask = backgroundSubstractor.apply(frame) # FIXME: Is this the right subtraction?
        original_mask = mask.copy()

        if args['remove_shadows']:
            mask = cv2.bitwise_and(mask, 255)

        # Smoothen a bit the mask to get back some of the missing pixels
        if args['blur'] > 0:
            mask = cv2.blur(mask, (args['blur'], args['blur']))

        ret, mask = cv2.threshold(mask, 1, 255, cv2.THRESH_BINARY)

        corners = [[0, 0], [height - 1, 0], [0, width - 1], [height - 1, width - 1]]

        score = cv2.countNonZero(mask)
        print("Starting with a score of %d." % score)
        if args['fill_holes'] and score != surface:
            # Attempt to fill any holes.
            # At this stage, often, we have a mask surrounded by black and containing holes.
            # (this is not always the case -  sometimes, the mask is a cloud of points).
            positive = mask.copy()
            fill_mask = numpy.zeros((height + 2, width + 2), numpy.uint8)
            found = False
            for y,x in corners:
                if positive[y, x] == 0:
                    cv2.floodFill(positive, fill_mask, (x, y), 255)
                    found = True
                    break

            if found:
                filled = cv2.bitwise_or(mask, cv2.bitwise_not(positive))

                # Check if we haven't filled too many things, in which case
                # our fill operation actually decreased the quality of the
                # image.
                filled_score = cv2.countNonZero(filled)
                if filled_score < surface * .9:
                    has_empty_corners = False
                    for y, x in corners:
                        if filled[y, x] == 0:
                            has_empty_corners = True
                            break
                    if has_empty_corners:
                        # Apparently, we have managed to remove holes, without filling
                        # the entire frame.
                        score = filled_score
                        mask = filled
                        print("Improved to a score of %d" % score)

        bw_mask = mask

        if args['show']:
            cv2.imshow('mask', mask)
            cv2.moveWindow('mask', args['width'] + 32, args['height'] + 32)

        if args['use_contour']:
            image, contours, hierarchy = cv2.findContours(bw_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)

            # FIXME: We could remove small contours (here or later)
            bw_mask = numpy.zeros((height, width), numpy.uint8)
            for cnt in contours:
                if cv2.contourArea(cnt) > args['min_size'] or 0:
                    hull = cv2.convexHull(cnt)
                    cv2.fillPoly(bw_mask, [hull], 255, 8)

            if args['contours_prefix']:
                dest = "%s_%d.png" % (args['contours_prefix'], i)
                print("Writing contours to %s." % dest)
                cv2.imwrite(dest, bw_mask)

            print("contours: %d, score: %d" % (len(contours), score))

        score = cv2.countNonZero(bw_mask)
        mask = cv2.cvtColor(bw_mask, cv2.COLOR_GRAY2RGB)
        extracted = cv2.bitwise_and(mask, frame)
        if args['show']:
            cv2.imshow('extracted', extracted)
            cv2.moveWindow('extracted', 0, args['height'] + 32)

        if score != surface:
            # We have captured the entire image. Definitely not a good thing to do.
            if i > len(frames) * args['buffer_init'] or i + 1 == len(frames):
                # We are done buffering
                candidates.append((score, mask, bw_mask, original_mask, extracted, i, 0))

        latest_score = score

    candidates.sort(key=lambda tuple: tuple[0], reverse=True)
    candidates = candidates[:args['keep']]

    for candidate_index, candidate in enumerate(candidates):
        best_score, best_mask, best_bw_mask, best_original_mask, best_extracted, best_index, best_perimeter = candidate

        print ("Best score %d/%s" % (best_score, best_perimeter))

# Get rid of small components
        if args['min_size'] > 0:
            number, components = cv2.connectedComponents(best_bw_mask)
            flattened = components.flatten()
            stats = numpy.bincount(flattened)
            # FIXME: Optimize this
            removing = 0
            for i, stat in enumerate(stats):
                if stat == 0:
                    continue
                if stat < args['min_size']:
                    kill_list = components == i
                    best_mask[kill_list] = 0
                    best_extracted[kill_list] = 0
                    removing += 1

# Add transparency
#        cv2.mixChannels([best_extracted, best_mask], [0, 0, 1, 1, 2, 2, ])
        split_1, split_2, split_3 = cv2.split(best_extracted)
        transparency = cv2.merge([split_1, split_2, split_3, best_bw_mask])
        if args['objects_prefix']:
            dest = "%s_%d.png" % (args['objects_prefix'], candidate_index)
            print("Writing object to %s." % dest)
            cv2.imwrite(dest, transparency)
        if args['masks_prefix']:
            dest = "%s_%d.png" % (args['masks_prefix'], candidate_index)
            print("Writing mask to %s." % dest)
            cv2.imwrite(dest, best_original_mask)

    cv2.destroyAllWindows()


def stabilize(frames):
    # Accumulated frame transforms.
    acc_dx = 0
    acc_dy = 0
    acc_da = 0

    acc_transform = numpy.zeros((3, 3), numpy.float32)
    acc_transform[0, 0] = 1
    acc_transform[1, 1] = 1
    acc_transform[2, 2] = 1

    # Highest translations (left/right, top/bottom), used to compute a mask
    min_acc_dx = 0
    max_acc_dx = 0
    min_acc_dy = 0
    max_acc_dy = 0

    stabilized = []

    prev = frames[0]
    prev_gray = cv2.cvtColor(prev, cv2.COLOR_RGB2GRAY)

    stabilized_writer = None

    if args['dump_stabilized']:
        stabilized_writer = cv2.VideoWriter(args['dump_stabilized'], cv2.VideoWriter_fourcc(*"DIVX"), 16, (args['width'], args['height']));

    # Stabilize image, most likely introducing borders.
    stabilized.append(prev)
    for cur in frames[1:]:
        cur_gray = cv2.cvtColor(cur, cv2.COLOR_RGB2GRAY)

        prev_corner = cv2.goodFeaturesToTrack(prev_gray, maxCorners=200, qualityLevel=.01, minDistance=10) # FIXME: What are these constants?
        if not (prev_corner is None):
            # FIXME: Really, what should we do if `prev_corner` is `None`?
            cur_corner, status, err = cv2.calcOpticalFlowPyrLK(prev_gray, cur_gray, prev_corner, None)
            should_copy = [bool(x) for x in status]

            # weed out bad matches
            # FIXME: I'm sure that there is a more idiomatic way to do this in Python
            corners = len(should_copy)
            prev_corner2 = numpy.zeros((corners, 1, 2), numpy.float32)
            cur_corner2 = numpy.zeros((corners, 1, 2), numpy.float32)

            j = 0
            for i in range(len(status)):
                if status[i]:
                    prev_corner2[j] = prev_corner[i]
                    cur_corner2[j] = cur_corner[i]
                    j += 1
            prev_corner = None
            cur_corner = None


            # Compute transformation between frames, as a combination of translations, rotations, uniform scaling.
            transform = cv2.estimateRigidTransform(prev_corner2, cur_corner2, False)
            if transform is None:
                print("stabilize: could not find transform, skipping frame")
            else:
                dx = transform[0, 2]
                dy = transform[1, 2]

                result = None

                if dx == 0. and dy == 0.:
                    print("stabilize: dx and dy are 0")
                    # For some reason I don't understand yet, if both dx and dy are 0,
                    # our matrix multiplication doesn't seem to make sense.
                    result = cur
                else:
                    da = math.atan2(transform[1, 0], transform[0, 0])

                    acc_dx += dx
                    if acc_dx > max_acc_dx:
                        max_acc_dx = acc_dx
                    elif acc_dx < min_acc_dx:
                        min_acc_dx = acc_dx

                    acc_dy += dy
                    if acc_dy > max_acc_dy:
                        max_acc_dy = acc_dy
                    elif acc_dy < min_acc_dy:
                        min_acc_dy = acc_dy

                    acc_da += da

                    padded_transform = numpy.zeros((3, 3), numpy.float32)
                    for i in range(2):
                        for j in range(3):
                            padded_transform[i,j] = transform[i,j]
                    padded_transform[2, 2] = 1
                    acc_transform = numpy.dot(acc_transform, padded_transform)

                    print("stabilize: current transform\n %s" % transform)
                    print("stabilize: padded transform\n %s" % padded_transform)
                    print("stabilize: full transform\n %s" % acc_transform)
                    print("stabilize: resized full transform\n %s" % numpy.round(acc_transform[0:2, :]))
                    result = cv2.warpAffine(cur, numpy.round(acc_transform[0:2,:]), (args['width'], args['height']), cv2.INTER_NEAREST)
                stabilized.append(result)

                if stabilized_writer:
                    stabilized_writer.write(result)
        else:
            print("stabilize: could not find prev_corner, skipping frame")

        prev = cur
        prev_gray = cur_gray

    # Now crop all images to remove these borders.
    cropped = []
    for frame in stabilized:
#        cropped.append(frame[max_acc_dx:max_acc_dy, min_acc_dx:min_acc_dy])
        cropped.append(frame)
        # FIXME: Actually crop

    return cropped

def compute_diff(a, b):
    """Compute the number of pixels different in a and b"""
    # Fun fact: It's faster to substract, then convert colors, then
    # count non-zeros than to do a Python loop.
    return cv2.norm(a, b)


if __name__ == '__main__':
    sys.exit(main())