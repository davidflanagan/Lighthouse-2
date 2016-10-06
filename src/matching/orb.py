import argparse
import glob
import cv2

GOOD_RATIO_VALUE = 0.75
NUMBER_OF_FEATURES = 2000

parser = argparse.ArgumentParser(
    description='Finds the best match for the input image among the images in the provided folder.')
parser.add_argument('-t', '--template', required=True, help='Path to the image we would like to find match for')
parser.add_argument('-i', '--images', required=True, help='Path to the folder with the images we would like to match')
args = vars(parser.parse_args())

# Load the image and convert it to grayscale.
template = cv2.imread(args["template"])
gray_template = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)

# Initialize the ORB descriptor, then detect keypoints and extract local invariant descriptors from the image.
detector = cv2.ORB_create(nfeatures=NUMBER_OF_FEATURES)

# Create Brute Force matcher.
matcher = cv2.BFMatcher(cv2.NORM_HAMMING)

(template_keypoints, template_descriptors) = detector.detectAndCompute(gray_template, None)

statistics = []

# loop over the images to find the template in
for image_path in glob.glob(args["images"] + "/*.jpg"):
    # Load the image, convert it to grayscale.
    image = cv2.imread(image_path)
    gray_image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    (image_keypoints, image_descriptors) = detector.detectAndCompute(gray_image, None)

    matches = matcher.knnMatch(template_descriptors, image_descriptors, k=2)

    # Apply ratio test.
    good_matches = []
    for m, n in matches:
        if m.distance < GOOD_RATIO_VALUE * n.distance:
            good_matches.append([m])

    statistics.append((image_path, image_keypoints, matches, good_matches, image))

statistics = sorted(statistics, key=lambda (v, w, x, y, z): len(y), reverse=True)

for idx, (path, keypoints, matches, good_matches, image) in enumerate(statistics):
    if idx < 3:
        result_image = cv2.drawMatchesKnn(template, template_keypoints, image, keypoints, good_matches, None, flags=2)
        cv2.imshow("Best match #" + str(idx + 1), result_image)
    print("{}: {} - {}".format(path, len(matches), len(good_matches)))

cv2.waitKey(0)
