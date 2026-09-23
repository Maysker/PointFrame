(function(root,factory){const api=factory();if(typeof module!=="undefined"&&module.exports)module.exports=api;else root.AngleRuler=api})(typeof window!=="undefined"?window:globalThis,function(){
  "use strict";
  function wrap(value,min,max){const span=max-min;return((value-min)%span+span)%span+min}
  function constrain(value,options){return options.cyclic?wrap(value,options.min,options.max):Math.max(options.min,Math.min(options.max,value))}
  function dragValue(start,pixels,options){return constrain(start-pixels/options.pixelsPerDegree,options)}
  function create(element,options){
    const settings={orientation:"horizontal",min:-180,max:180,cyclic:false,pixelsPerDegree:4,...options};
    const canvas=element.querySelector("canvas"),
      context=canvas.getContext("2d"),
      valueNode=element.parentElement.querySelector("[data-angle-value]");
    let value=constrain(Number(settings.value)||0,settings),drag=null;
    function display(angle){const rounded=Math.abs(angle)<.05?0:angle;return `${rounded.toFixed(1)}°`}
    function draw(){
      const ratio=window.devicePixelRatio||1,width=canvas.clientWidth,height=canvas.clientHeight;
      if(canvas.width!==Math.round(width*ratio)||canvas.height!==Math.round(height*ratio)){canvas.width=Math.round(width*ratio);canvas.height=Math.round(height*ratio)}
      context.setTransform(ratio,0,0,ratio,0,0);context.clearRect(0,0,width,height);
      const horizontal=settings.orientation==="horizontal",length=horizontal?width:height,center=length/2;
      const first=Math.floor(value-center/settings.pixelsPerDegree)-1,last=Math.ceil(value+center/settings.pixelsPerDegree)+1;
      context.strokeStyle="#9fb2ba";context.fillStyle="#d4e0e4";context.lineWidth=1;context.font="10px system-ui,sans-serif";context.textAlign=horizontal?"center":"right";context.textBaseline=horizontal?"top":"middle";
      for(let raw=first;raw<=last;raw++){
        if(!settings.cyclic&&(raw<settings.min||raw>settings.max))continue;
        const position=center+(raw-value)*settings.pixelsPerDegree,major=raw%10===0,medium=raw%5===0,size=major?12:medium?8:5;
        context.beginPath();
        if(horizontal){context.moveTo(position,height);context.lineTo(position,height-size)}
        else{context.moveTo(width,position);context.lineTo(width-size,position)}
        context.stroke();
        if(major){const label=settings.cyclic?constrain(raw,settings):raw;if(horizontal)context.fillText(`${label}°`,position,2);else context.fillText(`${label}°`,width-size-4,position)}
      }
      valueNode.textContent=display(value);
    }
    function setValue(next){value=constrain(next,settings);draw()}
    function coordinate(event){return settings.orientation==="horizontal"?event.clientX:event.clientY}
    function pointerDown(event){if(event.button!==0)return;drag={coordinate:coordinate(event),value};element.setPointerCapture(event.pointerId);element.classList.add("dragging");event.preventDefault()}
    function pointerMove(event){if(!drag)return;const next=dragValue(drag.value,coordinate(event)-drag.coordinate,settings);if(next===value)return;value=next;draw();settings.onChange?.(value)}
    function pointerUp(event){if(!drag)return;drag=null;element.classList.remove("dragging");if(element.hasPointerCapture(event.pointerId))element.releasePointerCapture(event.pointerId)}
    element.addEventListener("pointerdown",pointerDown);element.addEventListener("pointermove",pointerMove);element.addEventListener("pointerup",pointerUp);element.addEventListener("pointercancel",pointerUp);
    new ResizeObserver(draw).observe(element);draw();
    return{setValue,getValue:()=>value,draw};
  }
  return{wrap,constrain,dragValue,create};
});
