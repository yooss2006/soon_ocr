"""Local Pix2Text MFR 1.5 inference, using the model's published preprocessing.

Generation confidence is an uncertainty signal, not proof of correctness.
"""
import json
from pathlib import Path
import numpy as np
import onnxruntime as ort
from PIL import Image
from tokenizers import Tokenizer


class FormulaRecognizer:
    def __init__(self, directory):
        directory=Path(directory)
        opts=ort.SessionOptions();opts.intra_op_num_threads=4
        self.encoder=ort.InferenceSession(str(directory/'encoder_model.onnx'),sess_options=opts,providers=['CPUExecutionProvider'])
        self.decoder=ort.InferenceSession(str(directory/'decoder_model.onnx'),sess_options=opts,providers=['CPUExecutionProvider'])
        self.tokenizer=Tokenizer.from_file(str(directory/'tokenizer.json'))
        self.preprocess=json.loads((directory/'preprocessor_config.json').read_text())
        self.generation=json.loads((directory/'generation_config.json').read_text())

    def recognize(self,image,max_tokens=256):
        p=self.preprocess
        image=image.convert('RGB').resize((p['size']['width'],p['size']['height']),Image.Resampling.BICUBIC)
        pixels=np.asarray(image,dtype=np.float32)*p['rescale_factor']
        pixels=(pixels-np.array(p['image_mean'],dtype=np.float32))/np.array(p['image_std'],dtype=np.float32)
        pixels=np.ascontiguousarray(pixels.transpose(2,0,1)[None])
        encoded=self.encoder.run(None,{'pixel_values':pixels})[0]
        tokens=[self.generation['decoder_start_token_id']];log_probs=[];complete=False
        for _ in range(max_tokens):
            logits=self.decoder.run(None,{'input_ids':np.array([tokens],dtype=np.int64),'encoder_hidden_states':encoded})[0][0,-1]
            token=int(logits.argmax())
            shifted=logits-logits.max()
            log_probs.append(float(shifted[token]-np.log(np.exp(shifted).sum())))
            tokens.append(token)
            if token==self.generation['eos_token_id']:
                complete=True;break
        return {'latex':self.tokenizer.decode(tokens,skip_special_tokens=True),
                'confidence':round(float(np.exp(np.mean(log_probs))),5),
                'complete':complete,'tokens':len(tokens)-1}
