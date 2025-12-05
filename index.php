<!DOCTYPE html>
<html lang="en">
<head>
	<meta charset="UTF-8">
	<meta name="viewport" content="width=device-width, initial-scale=1.0">
	<title><?php echo $_SERVER['SERVER_NAME']?></title>
	<link
    rel="stylesheet"
    href="https://cdnjs.cloudflare.com/ajax/libs/animate.css/4.1.1/animate.min.css"
  />
  <style>
      body { margin:0 auto; display: grid; font-family: sans-serif;  min-height:100vh; }
      h3  { font-size:4em; margin:0px auto; }
      h4  { font-size:2em; margin:0px auto; }  
      .top { background:white; color: black; display: grid; justify-content: center; align-items: end; padding-bottom:3em; }
      .bottom { background: black; color:white; display: grid; justify-content: center; align-items: start; padding-top:3em; }
      .footer { height:30px; background: black; color:white; }
      
  </style>
</head>
<body>
	
    <div class="top">
       <h3 class="animate__animated animate__fadeInTopLeft"><?php echo $_SERVER['SERVER_NAME']?></h3> 
    </div>
    <div class="bottom">
        <h4 class="animate__animated animate__fadeInBottomRight">Coming Soon</h4>
    </div>
<!-- <div class="footer">
    Registered by TechSoul
</div> -->



</body>
</html>