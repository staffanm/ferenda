/* own code goes here */

$( document ).ready(function() {
    $( "a.navbutton" ).click(function( event ) {
	$( "header nav ul" ).slideToggle("fast");
    });
    $( "a.searchbutton" ).click(function( event ) {
	$( "header form input" ).slideToggle("fast");
    });
    $( "a.tocbutton" ).click(function( event ) {
	$( "nav#toc ul" ).slideToggle("fast");
    });
    $( "nav#toc ul li" ).click(function( event ) {
	$(this).children("ul").toggle()
    });
    
});
	
