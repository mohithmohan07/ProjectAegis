"""Supplement the reused publisher's HTTP/hash check with JPEG dimensions."""
import hashlib,json,struct,sys,urllib.request
from pathlib import Path
def dimensions(data):
    assert data[:2]==b'\xff\xd8'
    i=2
    while i<len(data):
        assert data[i]==255
        while data[i]==255:i+=1
        marker=data[i];i+=1
        if marker in (0xd8,0xd9):continue
        n=int.from_bytes(data[i:i+2],'big')
        if marker in (0xc0,0xc1,0xc2,0xc3,0xc5,0xc6,0xc7,0xc9,0xca,0xcb,0xcd,0xce,0xcf):
            height,width=struct.unpack('>HH',data[i+3:i+7]);return width,height
        i+=n
    raise ValueError('No JPEG frame')
manifest=json.loads((Path(sys.argv[1])/'manifest.json').read_text())
report=[]
for a in manifest['assets']:
    with urllib.request.urlopen(a['url'],timeout=30) as r:
        data=r.read(); assert r.status==200 and r.headers.get_content_type()=='image/jpeg'
    assert len(data)==a['bytes'] and hashlib.sha256(data).hexdigest()==a['sha256']
    assert dimensions(data)==(a['width'],a['height'])
    result={**a,'status':200,'mime':'image/jpeg','verified':True};report.append(result)
    print('SCIENCE_IMAGE_VERIFIED '+json.dumps(result),flush=True)
Path('science-image-verification.json').write_text(json.dumps(report,indent=2))
print('ALL_SCIENCE_IMAGE_URLS_VERIFIED',len(report),flush=True)
