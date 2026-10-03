import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from petpark.faithful_cards import Composition, render_game_card
from test_pet_card_pillow import snapshot


class FaithfulLayoutTests(unittest.TestCase):
    def test_pet_portrait_does_not_shrink_with_the_760px_canvas(self):
        rectangles=[]
        original=Composition.picture
        def record(p,n,x,y,w,h,paint,**kwargs):
            if paint:rectangles.append((x,y,w,h))
            return original(p,n,x,y,w,h,paint,**kwargs)
        with patch.object(Composition,'picture',record):
            image=render_game_card(snapshot(),760)
        self.assertEqual(image.width,760)
        self.assertEqual(len(rectangles),1)
        self.assertEqual(rectangles[0][3],308)
        self.assertGreater(rectangles[0][2],310)

    def test_long_pet_values_are_complete_and_painted_inside_the_card(self):
        skill='九霄御风诀、'*60
        html=snapshot(skill,extra='<div class="row"><k>余量</k><v>123456789012345678901234567890 经验</v></div>')
        html=html.replace('<strong>860</strong>','<strong>'+str(10**55)+'</strong>')
        audit=[];bounds=[];original=Composition.text
        def record(p,value,x,y,width,*args,**kwargs):
            height=original(p,value,x,y,width,*args,**kwargs)
            paint=kwargs.get('paint',args[3] if len(args)>3 else False)
            if paint and value:bounds.append((value,x,y,width,height))
            return height
        with patch.object(Composition,'text',record):
            image=render_game_card(html,760,audit)
        short=render_game_card(snapshot(),760)
        self.assertGreater(image.height,short.height)
        self.assertIn(skill,audit)
        self.assertIn('123456789012345678901234567890 经验',audit)
        self.assertIn(str(10**55),audit)
        for value,x,y,w,h in bounds:
            self.assertGreaterEqual(x,0,value)
            self.assertLessEqual(x+w,image.width+1,value)
            self.assertLessEqual(y+h,image.height,value)


if __name__=='__main__':unittest.main()
