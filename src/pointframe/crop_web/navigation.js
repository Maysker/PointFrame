(function(root,factory){const api=factory();if(typeof module!=="undefined"&&module.exports)module.exports=api;else root.CropNavigation=api})(typeof window!=="undefined"?window:globalThis,function(){
  "use strict";
  const WORLD_UP=Object.freeze([0,0,1]);
  const ORBIT_RADIANS_PER_VIEWPORT=105*Math.PI/180;
  const MAX_TOP_TILT=30*Math.PI/180;
  const FIXED_BASES=Object.freeze({
    top:Object.freeze({right:Object.freeze([1,0,0]),up:Object.freeze([0,1,0]),depth:Object.freeze([0,0,1])}),
    front:Object.freeze({right:Object.freeze([-1,0,0]),up:Object.freeze([0,0,1]),depth:Object.freeze([0,1,0])}),
    side:Object.freeze({right:Object.freeze([0,1,0]),up:Object.freeze([0,0,1]),depth:Object.freeze([1,0,0])}),
  });
  const dot=(a,b)=>a[0]*b[0]+a[1]*b[1]+a[2]*b[2];
  const cross=(a,b)=>[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]];
  function normalizeVector(vector){const length=Math.hypot(...vector);if(length<1e-12)throw Error("Degenerate view axis");return vector.map(value=>value/length)}
  function clampTopTilt(tiltX,tiltY){
    const magnitude=Math.hypot(tiltX,tiltY);
    if(magnitude<=MAX_TOP_TILT)return{tiltX,tiltY};
    const scale=MAX_TOP_TILT/magnitude;
    return{tiltX:tiltX*scale,tiltY:tiltY*scale};
  }
  function topTiltBasis(camera){
    const magnitude=Math.hypot(camera.tiltX,camera.tiltY);
    if(magnitude<1e-15)return{right:[1,0,0],up:[0,1,0],depth:[0,0,1]};
    const axis=[-camera.tiltY/magnitude,camera.tiltX/magnitude,0];
    return{right:rotateAround([1,0,0],axis,magnitude),up:rotateAround([0,1,0],axis,magnitude),depth:rotateAround([0,0,1],axis,magnitude)};
  }
  function topTiltFromBasis(viewBasis,distance=1,target=[0,0,0]){
    if(!(distance>0))throw Error("Camera distance must be positive");
    const depth=normalizeVector(viewBasis.depth),horizontal=Math.hypot(depth[0],depth[1]),magnitude=Math.min(MAX_TOP_TILT,Math.atan2(horizontal,Math.max(-1,Math.min(1,depth[2]))));
    const tiltX=horizontal>1e-12?depth[0]*magnitude/horizontal:0,tiltY=horizontal>1e-12?depth[1]*magnitude/horizontal:0;
    return{tiltX,tiltY,distance,target:[...target]};
  }
  function topTiltDrag(state,dx,dy,width,height){
    if(width<=0||height<=0)throw Error("Top-tilt viewport must be positive");
    const tilt=clampTopTilt(state.tiltX+dx*ORBIT_RADIANS_PER_VIEWPORT/width,state.tiltY-dy*ORBIT_RADIANS_PER_VIEWPORT/height);
    return{...tilt,distance:state.distance,target:[...state.target]};
  }
  function rotateAround(vector,axis,angle){const unit=normalizeVector(axis),cosine=Math.cos(angle),sine=Math.sin(angle),projection=dot(unit,vector)*(1-cosine),perpendicular=cross(unit,vector);return vector.map((value,index)=>value*cosine+perpendicular[index]*sine+unit[index]*projection)}
  function orthonormalBasis(viewBasis){const right=normalizeVector(viewBasis.right),up0=viewBasis.up.map((value,index)=>value-dot(viewBasis.up,right)*right[index]),up=normalizeVector(up0),depth=normalizeVector(cross(right,up));return{right,up,depth}}
  function wrapAngle(angle){return((angle+Math.PI)%(2*Math.PI)+2*Math.PI)%(2*Math.PI)-Math.PI}
  function angleDelta(next,current){return wrapAngle(next-current)}
  function rotateBasisAround(viewBasis,axis,angle){
    if(angle===0)return viewBasis;
    return orthonormalBasis({right:rotateAround(viewBasis.right,axis,angle),up:rotateAround(viewBasis.up,axis,angle),depth:rotateAround(viewBasis.depth,axis,angle)});
  }
  function freeOrbitDrag(state,dx,dy,width,height){
    if(width<=0||height<=0)throw Error("Free-orbit viewport must be positive");
    let result=orthonormalBasis(state.basis);
    if(dx!==0){const angle=dx*ORBIT_RADIANS_PER_VIEWPORT/width;result=orthonormalBasis({right:rotateAround(result.right,result.up,angle),up:result.up,depth:rotateAround(result.depth,result.up,angle)})}
    if(dy!==0){const angle=dy*ORBIT_RADIANS_PER_VIEWPORT/height;result=orthonormalBasis({right:result.right,up:rotateAround(result.up,result.right,angle),depth:rotateAround(result.depth,result.right,angle)})}
    return{basis:result,target:[...state.target]};
  }
  function precisionOrbitFromBasis(viewBasis){
    const basis=orthonormalBasis(viewBasis);
    const depth=basis.depth;
    const elevation=Math.asin(Math.max(-1,Math.min(1,depth[2])));
    const azimuth=Math.atan2(depth[0],depth[1]);
    return{azimuth,elevation};
  }
  function continuousOrbitFromBasis(viewBasis,previous=null){
    const basis=orthonormalBasis(viewBasis),depth=basis.depth,horizontal=Math.hypot(depth[0],depth[1]);
    if(!previous)return precisionOrbitFromBasis(basis);
    if(horizontal<1e-12)return{azimuth:wrapAngle(previous.azimuth),elevation:depth[2]>=0?Math.PI/2:-Math.PI/2};
    const azimuth=Math.atan2(depth[0],depth[1]),elevation=Math.asin(Math.max(-1,Math.min(1,depth[2])));
    const alternate={azimuth:wrapAngle(azimuth+Math.PI),elevation:wrapAngle(elevation>=0?Math.PI-elevation:-Math.PI-elevation)};
    const canonical={azimuth:wrapAngle(azimuth),elevation:wrapAngle(elevation)};
    const score=candidate=>angleDelta(candidate.azimuth,previous.azimuth)**2+angleDelta(candidate.elevation,previous.elevation)**2;
    return score(alternate)<score(canonical)?alternate:canonical;
  }

  function precisionOrbitBasis(state){
    const azimuth=state.azimuth;
    const elevation=state.elevation;

    const cosElevation=Math.cos(elevation);
    const sinElevation=Math.sin(elevation);
    const sinAzimuth=Math.sin(azimuth);
    const cosAzimuth=Math.cos(azimuth);

    const depth=[
      sinAzimuth*cosElevation,
      cosAzimuth*cosElevation,
      sinElevation,
    ];

    const right=[
      -cosAzimuth,
      sinAzimuth,
      0,
    ];

    const up=normalizeVector(cross(depth,right));

    return orthonormalBasis({right,up,depth});
  }
  function rollFromBasis(viewBasis){return Math.atan2(dot(viewBasis.right,WORLD_UP),dot(viewBasis.up,WORLD_UP))}
  function inspectStateFromView(viewBasis,pan,zoom,fit){
    if(!(zoom>0)||!(fit[0]>0)||!(fit[1]>0))throw Error("View scale must be positive");
    const horizontal=pan[0]/zoom/fit[0],vertical=pan[1]/zoom/fit[1],target=[0,1,2].map(index=>-horizontal*viewBasis.right[index]-vertical*viewBasis.up[index]);
    return{camera:topTiltFromBasis(viewBasis,1/zoom,target),target,pan:[0,0],zoom};
  }
  function topFrameFromView(viewBasis){const u0=normalizeVector(viewBasis.right),cameraViewDirection=normalizeVector(viewBasis.depth.map(value=>-value)),w=cameraViewDirection.map(value=>-value),v=normalizeVector(cross(w,u0)),u=normalizeVector(cross(v,w));return{u,v,w}}
  function resolveBasis(cameraOrBasis){return cameraOrBasis&&cameraOrBasis.right?cameraOrBasis:topTiltBasis(cameraOrBasis)}
  function cameraPosition(target,camera){const depth=topTiltBasis(camera).depth;return target.map((value,i)=>value+camera.distance*depth[i])}
  function panTarget(target,dx,dy,width,height,zoom,fit,cameraOrBasis){const b=resolveBasis(cameraOrBasis),horizontal=2*dx/width/zoom/fit[0],vertical=2*dy/height/zoom/fit[1];return target.map((value,i)=>value-horizontal*b.right[i]+vertical*b.up[i])}
  function visible(flags,index,mode){return mode==="all"||(mode==="selected"&&flags[index]>.5)||(mode==="excluded"&&flags[index]<.5)}
  function nearestProjectedPoint(positions,flags,mode,target,cameraOrBasis,width,height,zoom,fit,x,y,maxPixels=24){const b=resolveBasis(cameraOrBasis);let best=-1,bestDistance=maxPixels*maxPixels;for(let i=0;i<flags.length;i++){if(!visible(flags,i,mode))continue;const d=[positions[i*3]-target[0],positions[i*3+1]-target[1],positions[i*3+2]-target[2]],sx=width/2+dot(d,b.right)*zoom*fit[0]*width/2,sy=height/2-dot(d,b.up)*zoom*fit[1]*height/2,distance=(sx-x)**2+(sy-y)**2;if(distance<bestDistance){bestDistance=distance;best=i}}return best}
  function fitProjectedBounds(positions,flags,mode,target,viewBasis,width,height,margin=.92){
    if(width<=0||height<=0)throw Error("Fit viewport must be positive");
    let minX=Infinity,maxX=-Infinity,minY=Infinity,maxY=-Infinity,count=0;
    for(let i=0;i<flags.length;i++){if(!visible(flags,i,mode))continue;const d=[positions[i*3]-target[0],positions[i*3+1]-target[1],positions[i*3+2]-target[2]],x=dot(d,viewBasis.right),y=dot(d,viewBasis.up);minX=Math.min(minX,x);maxX=Math.max(maxX,x);minY=Math.min(minY,y);maxY=Math.max(maxY,y);count++}
    if(!count)return null;
    const centerX=(minX+maxX)/2,centerY=(minY+maxY)/2,rangeX=Math.max(maxX-minX,1e-9),rangeY=Math.max(maxY-minY,1e-9),aspect=width/height,fit=aspect>1?[1/aspect,1]:[1,aspect];
    const zoom=Math.max(.03,Math.min(100,2*margin/(rangeX*fit[0]),2*margin/(rangeY*fit[1])));
    const centeredTarget=target.map((value,i)=>value+centerX*viewBasis.right[i]+centerY*viewBasis.up[i]);
    return{count,zoom,fit,center:[centerX,centerY],target:centeredTarget,pan:[-centerX*zoom*fit[0],-centerY*zoom*fit[1]],bounds:[minX,maxX,minY,maxY]};
  }
  return{
    WORLD_UP,
    ORBIT_RADIANS_PER_VIEWPORT,
    MAX_TOP_TILT,
    FIXED_BASES,
    clampTopTilt,
    topTiltBasis,
    topTiltFromBasis,
    topTiltDrag,
    freeOrbitDrag,
    wrapAngle,
    angleDelta,
    rotateBasisAround,
    precisionOrbitFromBasis,
    continuousOrbitFromBasis,
    precisionOrbitBasis,
    rollFromBasis,
    inspectStateFromView,
    topFrameFromView,
    cameraPosition,
    panTarget,
    nearestProjectedPoint,
    fitProjectedBounds
  };
});
