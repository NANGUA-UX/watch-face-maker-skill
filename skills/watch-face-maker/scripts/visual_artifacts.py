"""从同一组输入生成和复核视觉对比像素，不判断视觉质量。"""


def comparison_images(reference, implementation, size, crops):
    from PIL import Image, ImageOps

    width, height = size
    reference = ImageOps.fit(reference.convert('RGBA'), size, method=Image.Resampling.LANCZOS)
    implementation = ImageOps.fit(implementation.convert('RGBA'), size, method=Image.Resampling.LANCZOS)
    side = Image.new('RGBA', (width * 2, height))
    side.paste(reference, (0, 0))
    side.paste(implementation, (width, 0))
    images = {'reference_fitted': reference, 'implementation_fitted': implementation,
              'side_by_side': side, 'overlay': Image.blend(reference, implementation, 0.5)}
    for name, box in crops:
        x1, y1, x2, y2 = box
        pair = Image.new('RGBA', ((x2 - x1) * 2, y2 - y1))
        pair.paste(reference.crop(box), (0, 0))
        pair.paste(implementation.crop(box), (x2 - x1, 0))
        images[name] = pair
    return images
