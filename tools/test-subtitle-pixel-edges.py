#!/usr/bin/env python3
"""Production pixel extraction -> consensus/snap -> publication -> real libass bounds.

Controlled images demonstrate estimator failure, not the unseen pixels in '9'.
Decode/VFS and GPU are boundaries, as in the retained connected fixture.
"""
import argparse
from pathlib import Path
import runpy
import subprocess

ROOT = Path(__file__).resolve().parents[1]
DETECTOR = runpy.run_path(str(ROOT / 'tools/test-subtitle-active-area-detection.py'))
TEXT = runpy.run_path(str(ROOT / 'tools/test-subtitle-text-integration.py'))


def generate(baseline=None):
    aml = TEXT['source']('xbmc/utils/AMLUtils.cpp', baseline)
    pixel = DETECTOR['source'](aml)
    prefix = DETECTOR['PREFIX']
    formats = prefix[prefix.index('constexpr int AV_PIX_FMT'):prefix.index('constexpr int DV_DETECT')]
    frame = prefix[prefix.index('struct Frame '):prefix.index('struct DetectResult ')]
    bodies = pixel[pixel.index('bool AdmitFrame('):pixel.index('DOVIFrameMetadata CDataCacheCore::')]
    # Renderer/lifecycle code stays at the current version. Only the pixel
    # estimator changes in the original-versus-corrected comparison.
    connected = TEXT['generate']().replace('int main(int argc,char** argv)', 'void retainedMain(int argc,char** argv)', 1)
    return connected + formats + frame + '\nusing Result=DetectResult;\nconstexpr int DV_DETECT_SKIP_IMAX=6;\n' + bodies + TESTS


TESTS = r'''
struct PixelImage {
 Frame frame;Source source;std::vector<uint8_t> bytes;
 PixelImage(int width=1920,int height=1080):bytes(width*height,16){
  frame.width=source.width=width;frame.height=source.height=height;frame.linesize[0]=width;frame.data[0]=bytes.data();}
 void fill(int top,int bottom,int left,int right,int shoulder=26,int middle=66,int deepTop=123,int deepBottom=223){
  std::fill(bytes.begin(),bytes.end(),16);
  for(int y=top;y<frame.height-bottom;++y)for(int x=left;x<frame.width-right;++x){
   int value=shoulder;
   if(x>=frame.width/2-32&&x<frame.width/2+32)
    value=(y<deepTop||y>=frame.height-deepBottom)?24:middle;
   bytes[y*frame.width+x]=static_cast<uint8_t>(value);
  }
 }
 Result edges(){assert(AdmitFrame(&frame,&source));assert(Contrast(&frame)>=10);return Edges(&frame);}
};
bool sampleSelect(const std::vector<Result>& edges,int width,int height,DetectResult& result){
 assert(edges.size()==7);uint16_t t[7],b[7],l[7],r[7];
 for(int i=0;i<7;++i){t[i]=edges[i].top;b[i]=edges[i].bottom;l[i]=edges[i].left;r[i]=edges[i].right;}
 return Select(t,b,l,r,7,width,height,result);
}
DetectResult pixels(){
 PixelImage image;std::vector<Result> scans;DetectResult result{};
 for(int i=0;i<7;++i){
  image.fill(20,20,0,0,26+i%3,50+i*3,123+i*9,223+i*7);
  // Compressed/noisy near-black does not depend on a perfectly constant16.
  for(int y=0;y<20;++y)for(int x=0;x<1920;++x){
   image.bytes[y*1920+x]=static_cast<uint8_t>(14+(x+y+i)%5);
   image.bytes[(1079-y)*1920+x]=static_cast<uint8_t>(14+(x+y+i)%5);
  }
  scans.push_back(image.edges());
 }
 assert(sampleSelect(scans,1920,1080,result)&&result.top==21&&result.bottom==21&&result.left==0&&result.right==0);
 for(const auto& edge:scans)assert(edge.top==20&&edge.bottom==20&&edge.left==0&&edge.right==0);
 const auto accepted=result;
 // Replacing one sample with actual wider/narrower/full-frame picture keeps
 // the original smaller-bound and bilateral inward framing vetoes effective.
 image.fill(0,0,0,0,80,90,0,0);scans[6]=image.edges();
 assert(scans[6].top==0&&scans[6].bottom==0);assert(!sampleSelect(scans,1920,1080,result));
 image.fill(10,10,0,0);scans[6]=image.edges();assert(!sampleSelect(scans,1920,1080,result));
 image.fill(40,40,0,0);scans[6]=image.edges();assert(scans[6].top==40&&scans[6].bottom==40);assert(!sampleSelect(scans,1920,1080,result));
 // A small isolated caption in a bar outside the old centre strip is veto
 // evidence, not something to discard for failing the spatial quorum.
 image.fill(20,20,0,0);
 for(int x=0;x<64;++x)image.bytes[7*1920+x]=200;
 scans[6]=image.edges();assert(scans[6].top==0);assert(!sampleSelect(scans,1920,1080,result));
 image.fill(20,20,0,0);
 for(int y=7;y<20;++y)for(int x=928;x<992;++x)image.bytes[y*1920+x]=24;
 scans[6]=image.edges();assert(scans[6].top==0);assert(!sampleSelect(scans,1920,1080,result));
 // A persistent isolated earlier departure still cannot be overruled by
 // the other strips' majority at20 or by a deep original centre estimate.
 image.fill(20,20,0,0);
 for(int y=7;y<20;++y)for(int x=0;x<64;++x)image.bytes[y*1920+x]=200;
 scans[6]=image.edges();assert(scans[6].top==0);assert(!sampleSelect(scans,1920,1080,result));
 // Broad captions/graphics can meet the spatial quorum on both sides of
 // a genuinely larger bar. Their return to black must retain a framing veto.
 image.fill(40,40,0,0,90,90,40,40);
 for(int x=1200;x<1920;++x)image.bytes[1059*1920+x]=200;
 scans[6]=image.edges();assert(scans[6].top==40&&scans[6].bottom==0);assert(!sampleSelect(scans,1920,1080,result));
 // Dim full-width stripes evade the old midpoint but include the centre
 // as well as distributed strips; return-to-black remains an independent gate.
 image.fill(40,40,0,0,90,90,40,40);
 for(int y=20;y<28;++y)for(int x=0;x<1920;++x){image.bytes[y*1920+x]=24;image.bytes[(1079-y)*1920+x]=24;}
 scans[6]=image.edges();assert(scans[6].top==0&&scans[6].bottom==0);assert(!sampleSelect(scans,1920,1080,result));
 image.fill(40,40,0,0,90,90,40,40);
 for(int x=0;x<760;++x)image.bytes[20*1920+x]=200;
 for(int x=1200;x<1920;++x)image.bytes[1059*1920+x]=200;
 scans[6]=image.edges();assert(scans[6].top==0&&scans[6].bottom==0);assert(!sampleSelect(scans,1920,1080,result));
 // A broad graphic may persist all the way to the actual image boundary.
 // It must not turn a genuine larger pair into a tolerated unilateral outlier.
 image.fill(40,40,0,0,90,90,40,40);
 for(int y=20;y<40;++y)for(int x=1200;x<1920;++x)image.bytes[(1079-y)*1920+x]=200;
 scans[6]=image.edges();assert(scans[6].top==40&&scans[6].bottom==0);assert(!sampleSelect(scans,1920,1080,result));
 // The same safety decision covers thicker graphics and compressed stripes.
 image.fill(40,40,0,0,90,90,40,40);
 for(int y=20;y<28;++y){
  for(int x=0;x<760;++x)image.bytes[y*1920+x]=200;
  for(int x=1200;x<1920;++x)image.bytes[(1079-y)*1920+x]=200;
 }
 scans[6]=image.edges();assert(scans[6].top==0&&scans[6].bottom==0);assert(!sampleSelect(scans,1920,1080,result));
 // A bright outer strip is direct full-frame evidence; a dark/low-contrast
 // frame does not acquire usable evidence through the refinement.
 image.fill(20,20,0,0);for(int x=0;x<64;++x)image.bytes[x]=100;
 scans[6]=image.edges();assert(scans[6].top==0);assert(!sampleSelect(scans,1920,1080,result));
 std::fill(image.bytes.begin(),image.bytes.end(),16);assert(Contrast(&image.frame)<10);
 std::fill(image.bytes.begin(),image.bytes.end(),20);assert(Contrast(&image.frame)<10);
 // Uncorroborated earlier noise must not be counted as several agreeing
 // original bounds: unavailable lanes are not observations.
 image.fill(20,20,0,0,20,66,23,23);for(int x=0;x<64;++x)image.bytes[20*1920+x]=100;
 auto unsupported=image.edges();assert(unsupported.top==0);
 // Uniform genuinely thin bars retain the same measurement and common-AR snap.
 image.fill(20,20,0,0,90,90,20,20);auto clean=image.edges();assert(clean.top==20&&clean.bottom==20);
 // Unchanged wider HEVC geometry, including both supported10-bit layouts.
 PixelImage hevc(3840,2160);hevc.fill(262,262,0,0,28,100,846,527);
 auto edge=hevc.edges();assert(edge.top==262&&edge.bottom==262);
 std::vector<Result> wide(7,edge);assert(sampleSelect(wide,3840,2160,result)&&result.top==263&&result.bottom==263);
 std::vector<uint16_t> ten(hevc.bytes.size());
 for(size_t i=0;i<ten.size();++i)ten[i]=static_cast<uint16_t>(hevc.bytes[i]<<2);
 hevc.frame.data[0]=reinterpret_cast<uint8_t*>(ten.data());hevc.frame.linesize[0]=3840*2;hevc.frame.format=AV_PIX_FMT_YUV420P10LE;
 edge=hevc.edges();assert(edge.top==262&&edge.bottom==262);
 for(auto& value:ten)value<<=6;hevc.frame.format=AV_PIX_FMT_P010LE;
 edge=hevc.edges();assert(edge.top==262&&edge.bottom==262);
 // Transposed dark/nonuniform content exercises both independent pillar edges.
 PixelImage pillar;pillar.fill(0,0,240,240,28,100,0,0);
 for(int y=508;y<572;++y)for(int x=240;x<1680;++x)pillar.bytes[y*1920+x]=(x<500||x>=1400)?24:100;
 edge=pillar.edges();assert(edge.left==240&&edge.right==240&&edge.top==0&&edge.bottom==0);
 std::vector<Result> side(7,edge);assert(sampleSelect(side,1920,1080,result)&&result.left==240&&result.right==240);
 pillar.fill(0,0,300,300,28,100,0,0);for(int y=508;y<572;++y)for(int x=300;x<1620;++x)pillar.bytes[y*1920+x]=(x<500||x>=1400)?24:100;
 side[6]=pillar.edges();assert(!sampleSelect(side,1920,1080,result));
 // Exact logged arrays must STILL reject; these contain no new pixel evidence.
 uint16_t t[]={20,20,20,123,200,20,152},b[]={20,20,20,223,20,195,20},l[]={0,0,339,207,912,685,312},r[]={433,0,594,598,0,60,381};
 assert(!Select(t,b,l,r,7,1920,1080,result));
 std::cout<<"PASS: controlled1920 thin/noisy bars, dark shoulders, spatial captions, mixed/full frame, pillarbox and3840 10-bit edges through production consensus/snap\n";
 return accepted;
}
int main(int argc,char** argv){
 assert(argc==2&&ass_library_version()==LIBASS_VERSION&&LIBASS_VERSION>=0x01704000);
 auto result=pixels();ready();select(false,1920,1080);int before=writes.load();
 CRenderManager manager;manager.m_picture.iWidth=1920;manager.m_picture.iHeight=1080;manager.m_overlays.m_rs={0,0,1920,1080};
 auto c=cue(argv[1],"Tijd om te vertrekken.\\N- Hoezo? gypq");auto st=makeStyle();
 auto initial=draw(manager,c,st,true,true);assert(!s_detectSource->result);
 CAMLNativeWorker::Run run(s_detectSource->superseded);detect_publish(run,s_detectSource,result.top,result.bottom,result.left,result.right);
 uint16_t t,b,l,r;assert(aml_subtitle_detect_active_area_get(1920,1080,t,b,l,r)&&t==21&&b==21&&l==0&&r==0);
 assert(writes==before);auto corrected=draw(manager,c,st);rect(manager.m_overlays.m_activePicture,21,1059);
 inside(corrected.bounds,manager.m_overlays.m_activePicture);assert(corrected.bounds.bottom<=1059);
 assert(initial.bounds.bottom>=corrected.bounds.bottom);auto paused=draw(manager,c,st);assert(paused.canvas==corrected.canvas);
 s_detectWorker.Stop();std::cout<<"PASS: extracted thin-bar result -> source publication/getter -> real CE libass -> production atlas/vertices -> final alpha bounds21..1059; no non-DV sysfs writes\n";
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ass-include', type=Path, required=True)
    parser.add_argument('--ass-library', required=True)
    parser.add_argument('--font', type=Path, default=Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'))
    parser.add_argument('--baseline', help='Original estimator; must fail after successful strict compilation')
    parser.add_argument('--negative-controls', action='store_true')
    args = parser.parse_args()
    # The surrounding renderer stays current even in the pixel-baseline replay.
    revision = args.baseline
    args.baseline = None
    code = generate(revision)
    result = TEXT['run'](code, args, negative=bool(revision))
    if revision:
        assert 'sampleSelect(scans,1920,1080,result)' in result.stderr, result.stderr
        print('REJECTED original pixel estimator after strict compilation:', revision)
    elif args.negative_controls:
        for label, old, new in [
            ('centre-only edges', '  if (original == 0)', '  return original;\n  if (original == 0)'),
            ('bright centre still sets threshold', 'average(depth) > border + 6', 'average(depth) > 50'),
            ('earlier isolated content ignored', 'return support >= 3 ? earliest : 0;', 'return support >= 3 ? earliest : original;'),
            ('unobserved lanes counted', 'edges[lane] = UINT16_MAX;', 'edges[lane] = original;'),
            ('broad transient intrusion treated as picture', 'else if (edges[lane] != UINT16_MAX)', 'else if (false && edges[lane] != UINT16_MAX)'),
            ('sustained side graphic treated as picture', 'if (edges[lanes / 2] > earliest + tolerance)', 'if (false && edges[lanes / 2] > earliest + tolerance)'),
            ('bilateral mixed aspect veto removed', 'if (first[i] > candidate + tolerance && second[i] > candidate + tolerance)', 'if (false && first[i] > candidate + tolerance && second[i] > candidate + tolerance)'),
        ]:
            assert old in code, label
            TEXT['run'](code.replace(old, new, 1), args, negative=True)
            print('REJECTED compiled pixel control:', label)


if __name__ == '__main__':
    main()
