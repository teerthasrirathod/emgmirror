"""CT to patient-specific 3D models (research use, not for diagnosis or surgical planning).

Conventions shared by every module in this package:

* Images are ``SimpleITK.Image`` objects. Physical space is DICOM patient LPS in mm
  (x to patient left, y to posterior, z to head). Origin, spacing and direction always
  come from ImagePositionPatient / ImageOrientationPatient, never from SliceThickness.
* ``sitk.GetArrayViewFromImage(img)`` gives a NumPy array indexed ``[z, y, x]``.
* Intensities are Hounsfield units stored as int16.
* Label maps are uint8 on the same grid as the image they were made from, with the
  ids defined in :mod:`emgmirror.ct.labels`.
* Meshes are ``trimesh.Trimesh`` with vertices in patient LPS mm.
"""

DISCLAIMER = "Research use, not for diagnosis or surgical planning."
